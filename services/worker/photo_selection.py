"""Deterministic, local quality screening for eight identity-training photos.

This is a conservative image-quality heuristic, not identity verification or a
pose/beauty classifier. Heavy image dependencies are imported only when used.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import math
from pathlib import Path
import threading
import warnings


INPUT_COUNT = 8
SELECT_COUNT = 5
MIN_IMAGE_SIDE = 256
MIN_FACE_SIDE = 96
MAX_IMAGE_PIXELS = 40_000_000
MIN_SHARPNESS = 12.0
_DETECTOR_LOCK = threading.Lock()


def _libraries():
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Photo selection requires Pillow, numpy, and opencv-python-headless. "
            "Install the worker's image-analysis dependencies."
        ) from exc
    return cv2, np, Image, ImageOps


@lru_cache(maxsize=1)
def _face_detector():
    cv2, _, _, _ = _libraries()
    classifier = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    if classifier.empty():
        raise RuntimeError("OpenCV's built-in frontal-face detector is unavailable; reinstall opencv-python-headless.")
    return classifier


def _detect_faces(gray) -> list[tuple[int, int, int, int]]:
    """Return face boxes in original pixels; deliberately easy to mock in tests."""
    cv2, _, _, _ = _libraries()
    height, width = gray.shape
    scale = min(1.0, 1600.0 / max(width, height))
    work = gray if scale == 1.0 else cv2.resize(
        gray, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
    )
    with _DETECTOR_LOCK:
        boxes = _face_detector().detectMultiScale(
            cv2.equalizeHist(work), scaleFactor=1.1, minNeighbors=5, minSize=(48, 48)
        )
    return sorted(tuple(round(int(v) / scale) for v in box) for box in boxes)


def _difference_hash(gray, cv2) -> int:
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    value = 0
    for bit in (small[:, 1:] > small[:, :-1]).flat:
        value = (value << 1) | int(bit)
    return value


def _record(index: int) -> dict:
    return {"index": index, "selected": False, "score": 0.0, "reason": "", "metrics": {}}


def _analyze(path: Path, index: int) -> tuple[dict, dict | None]:
    cv2, np, Image, ImageOps = _libraries()
    record = _record(index)
    metrics = record["metrics"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if getattr(source, "n_frames", 1) != 1:
                    record["reason"] = "Replace this animated/multiframe file with one still photo."
                    return record, None
                width, height = source.size
                if width * height > MAX_IMAGE_PIXELS:
                    record["reason"] = "Resize this photo to at most 40 megapixels and upload it again."
                    return record, None
                image = ImageOps.exif_transpose(source)
                width, height = image.size
                metrics.update(width=int(width), height=int(height))
                if min(width, height) < MIN_IMAGE_SIDE:
                    record["reason"] = f"Photo is too small ({width}×{height}); replace it with an original at least 256 pixels on each side."
                    return record, None
                rgb = np.asarray(image.convert("RGB")).copy()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        record["reason"] = "Cannot read this photo; replace the corrupt or unsupported file with a readable JPEG, PNG, or WebP."
        metrics["read_error"] = type(exc).__name__
        return record, None

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    boxes = _detect_faces(gray)
    metrics["face_count"] = len(boxes)
    if not boxes:
        record["reason"] = "No clear frontal face detected; replace with a sharp, unobstructed front or slight-turn portrait."
        return record, None
    if len(boxes) != 1:
        record["reason"] = f"Detected {len(boxes)} faces; crop to only the intended person or replace the photo."
        return record, None

    x, y, face_width, face_height = (int(v) for v in boxes[0])
    right, bottom = min(width, x + face_width), min(height, y + face_height)
    x, y = max(0, x), max(0, y)
    face_width, face_height = right - x, bottom - y
    metrics["face_box"] = [x, y, face_width, face_height]
    metrics["face_min_side"] = min(face_width, face_height)
    if min(face_width, face_height) < MIN_FACE_SIDE:
        record["reason"] = "Face is too small; replace with a closer or higher-resolution photo showing a face at least 96 pixels across."
        return record, None

    face = gray[y:bottom, x:right]
    normalized_face = cv2.resize(face, (128, 128), interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(normalized_face, cv2.CV_64F).var())
    brightness = float(face.mean())
    dark_fraction = float((face <= 12).mean())
    light_fraction = float((face >= 243).mean())
    area_fraction = float(face_width * face_height / (width * height))
    metrics.update(
        sharpness=round(sharpness, 4), face_brightness=round(brightness, 4),
        dark_clipped_fraction=round(dark_fraction, 4), light_clipped_fraction=round(light_fraction, 4),
        face_area_fraction=round(area_fraction, 4),
    )
    if brightness < 20 or brightness > 238 or max(dark_fraction, light_fraction) > 0.80:
        record["reason"] = "Face detail is lost in extreme exposure; replace with a more evenly lit photo without crushed shadows or blown highlights."
        return record, None
    if sharpness < MIN_SHARPNESS:
        record["reason"] = "Face appears too blurry or detail-free; replace with a sharper original rather than an enlarged thumbnail."
        return record, None

    resolution_quality = min(1.0, min(face_width, face_height) / 320.0)
    sharpness_quality = min(1.0, math.log1p(sharpness) / math.log1p(600.0))
    # Penalize clipped detail, not normal differences in skin brightness.
    exposure_quality = max(0.0, 1.0 - dark_fraction - light_fraction)
    score = 100 * (0.40 * resolution_quality + 0.40 * sharpness_quality + 0.20 * exposure_quality)
    image_hash = _difference_hash(gray, cv2)
    face_hash = _difference_hash(face, cv2)
    digest = hashlib.sha256(f"{width}x{height}:".encode() + rgb.tobytes()).hexdigest()
    thumbnail = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    # Centering makes conservative duplicate checks tolerate small exposure edits.
    thumbnail -= float(thumbnail.mean())
    framing = "close" if area_fraction >= 0.25 else "medium" if area_fraction >= 0.08 else "wide"
    lighting = "lower" if brightness < 90 else "higher" if brightness > 170 else "middle"
    center = (x + face_width / 2) / width
    placement = "left" if center < 0.4 else "right" if center > 0.6 else "center"
    record["score"] = round(score, 4)
    metrics.update(
        image_hash=f"{image_hash:016x}", face_hash=f"{face_hash:016x}",
        framing=framing, brightness_band=lighting, horizontal_placement=placement,
    )
    internal = {
        "digest": digest, "image_hash": image_hash, "face_hash": face_hash,
        "thumbnail": thumbnail, "aspect": width / height,
    }
    return record, internal


def _duplicates(first: dict, second: dict) -> bool:
    if first["digest"] == second["digest"]:
        return True
    if abs(first["aspect"] - second["aspect"]) > 0.03:
        return False
    if (first["image_hash"] ^ second["image_hash"]).bit_count() > 4:
        return False
    if (first["face_hash"] ^ second["face_hash"]).bit_count() > 4:
        return False
    rmse = float(((first["thumbnail"] - second["thumbnail"]) ** 2).mean()) ** 0.5
    return rmse <= 0.04


def select_best_photos(paths: list[Path]) -> list[dict]:
    """Select five distinct usable photos and return records for all eight.

    Records retain input order and use zero-based ``index``. Scores and metrics
    contain only JSON-compatible primitives. Bad inputs are visibly rejected in
    ``reason``; insufficient usable, distinct inputs raise an actionable error.
    """
    if len(paths) != INPUT_COUNT:
        raise ValueError(f"Upload exactly 8 photos; received {len(paths)}.")
    analyzed = [_analyze(Path(path), index) for index, path in enumerate(paths)]
    records = [record for record, _ in analyzed]
    usable = sorted(
        (index for index, (_, internal) in enumerate(analyzed) if internal is not None),
        key=lambda index: (-records[index]["score"], index),
    )
    distinct: list[int] = []
    for index in usable:
        duplicate_of = next(
            (other for other in distinct if _duplicates(analyzed[index][1], analyzed[other][1])), None
        )
        if duplicate_of is not None:
            records[index]["metrics"]["duplicate_of"] = duplicate_of
            records[index]["reason"] = (
                f"Duplicate or near duplicate of photo {duplicate_of + 1}; use a different shot for useful training variety."
            )
        else:
            distinct.append(index)

    if len(distinct) < SELECT_COUNT:
        rejections = " ".join(
            f"Photo {record['index'] + 1}: {record['reason']}" for record in records if record["reason"]
        )
        needed = SELECT_COUNT - len(distinct)
        raise ValueError(
            f"Only {len(distinct)} distinct usable photos remain; need 5. "
            f"Replace at least {needed} rejected photo(s), then submit all 8 again. {rejections}"
        )

    chosen: list[int] = []
    seen = {"framing": set(), "brightness_band": set(), "horizontal_placement": set()}
    while len(chosen) < SELECT_COUNT:
        def rank(index: int) -> tuple[float, float, int]:
            metrics = records[index]["metrics"]
            bonus = 0.0 if not chosen else sum(
                weight for name, weight in (("framing", 3.0), ("brightness_band", 3.0), ("horizontal_placement", 2.0))
                if metrics[name] not in seen[name]
            )
            return records[index]["score"] + bonus, records[index]["score"], -index

        index = max((index for index in distinct if index not in chosen), key=rank)
        record = records[index]
        additions = [name for name in seen if record["metrics"][name] not in seen[name]]
        record["selected"] = True
        record["reason"] = "Selected: clear single face with usable detail and exposure."
        if chosen and additions:
            record["reason"] += " Adds framing or brightness variety."
        record["metrics"]["selection_rank"] = len(chosen) + 1
        chosen.append(index)
        for name in seen:
            seen[name].add(record["metrics"][name])

    for index in distinct:
        if not records[index]["selected"]:
            records[index]["reason"] = "Usable but not selected; five stronger or more varied photos were chosen."
    return records
