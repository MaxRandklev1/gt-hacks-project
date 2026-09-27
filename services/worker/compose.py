"""Automatic masks and CPU compositing for personal-base try-ons.

Personal base: the fixed pose with the person's own head, hair/head covering and skin (made once
at onboarding). Garment render: the same pose with the base model wearing a garment (made once
per garment). A try-on puts the garment region of the render onto the personal base, then puts
the person's hair/head covering back on top. Everything here is NumPy/OpenCV on the CPU, so scans
never wait for the GPU.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

try:
    from .parsing import LABELS, group_mask
except ImportError:
    from parsing import LABELS, group_mask


class CompositeError(ValueError):
    """Actionable, user-safe explanation."""


def _components(mask):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [(labels == index, stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)]


def _dilate(mask, pixels):
    if pixels <= 0:
        return mask.astype(bool)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pixels + 1, 2 * pixels + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel) > 0


def _fill_holes(mask):
    mask = mask.astype(np.uint8)
    flood = mask.copy()
    h, w = mask.shape
    canvas = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, canvas, (0, 0), 1)
    return (mask | (1 - flood)) > 0


def _keep_touching(mask, anchor, min_overlap=0.3):
    """Components of `mask` that mostly lie on `anchor` (e.g. the real head, not a printed face)."""
    kept = np.zeros(mask.shape, bool)
    for component, area in _components(mask):
        if (component & anchor).sum() >= min_overlap * area:
            kept |= component
    return kept


def head_crop(image, labels):
    """Head-and-shoulders crop of a selfie. Raises when no usable face was found."""
    face = group_mask(labels, "face")
    head = group_mask(labels, "head")
    components = sorted(_components(face), key=lambda item: -item[1])
    h, w = labels.shape
    if not components or components[0][1] < 0.01 * h * w:
        raise CompositeError("We couldn't find a clear face. Take a selfie facing the camera, in good light, with your whole face visible.")
    face = components[0][0]
    head = _keep_touching(head, _dilate(face, max(3, w // 50)), 0.05) | face
    ys, xs = np.nonzero(head)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    size = max(bottom - top, right - left)
    # Generous margin: hair volume, beard and the neck/shoulders give the model length cues.
    cx = (left + right) / 2
    box = (int(max(0, cx - 0.9 * size)), int(max(0, top - 0.25 * size)),
           int(min(w, cx + 0.9 * size)), int(min(h, bottom + 0.6 * size)))
    return image.crop(box)


def head_mask(labels, anchor_face=None):
    """Hair, head covering, face and glasses connected to the real head."""
    face = group_mask(labels, "face")
    if anchor_face is not None:
        face = _keep_touching(face, anchor_face, 0.3)
    else:
        parts = sorted(_components(face), key=lambda item: -item[1])
        face = parts[0][0] if parts else face
    head = group_mask(labels, "head")
    return _keep_touching(head, _dilate(face, max(3, labels.shape[1] // 60)), 0.05) | face


def covering_mask(labels):
    """Hair and head coverings attached to the real head: what may fall over a garment. The face and
    neck are excluded so collars and hoods correctly cover the neck."""
    head = head_mask(labels)
    return head & group_mask(labels, "hair")


def base_regions(base_labels):
    """Where the base model's real head and arms are; used to tell them apart from printed artwork."""
    return {"face": _dilate(group_mask(base_labels, "face"), base_labels.shape[1] // 40),
            "arms": _dilate(group_mask(base_labels, "arms"), base_labels.shape[1] // 60)}


def garment_mask(labels, regions):
    """Everything the garment covers in a garment render, including printed faces/arms/hair on it."""
    person = labels != LABELS.index("background")
    lower = np.isin(labels, [LABELS.index(n) for n in ("pants", "skirt", "belt", "left_leg", "right_leg", "left_shoe", "right_shoe", "bag")])
    head = head_mask(labels, regions["face"])
    arms = _keep_touching(group_mask(labels, "arms"), regions["arms"], 0.5)
    garment = person & ~lower & ~head & ~arms
    # Keep the main garment piece(s); drop specks, then close holes left by printed artwork.
    pieces = [c for c, area in _components(garment) if area > 0.002 * garment.size]
    garment = np.any(pieces, axis=0) if pieces else garment
    return _fill_holes(garment) & ~head & ~arms & ~lower


def alignment_score(a_labels, b_labels, include_face=True):
    """IoU of pose-defining regions (lower body + face) between two renders of the same pose.

    Arms are excluded: long sleeves legitimately cover them in garment renders.
    """
    def pose(labels):
        faces = sorted(_components(group_mask(labels, "face")), key=lambda item: -item[1])
        real_face = faces[0][0] if faces and include_face else np.zeros(labels.shape, bool)  # Not printed faces.
        return np.isin(labels, [LABELS.index(n) for n in ("pants", "skirt", "left_leg", "right_leg")]) | real_face
    a, b = pose(a_labels), pose(b_labels)
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def edit_region(base_labels, result_labels):
    """Where the personal base may differ from the pose base: head, hair/covering, and exposed skin."""
    w = base_labels.shape[1]
    base_head, result_head = head_mask(base_labels), head_mask(result_labels)
    skin = group_mask(base_labels, "arms") | group_mask(result_labels, "arms")
    return _dilate(base_head | result_head, w // 60) | _dilate(skin, w // 150)


def feather(mask, pixels):
    mask = mask.astype(np.float32)
    if pixels > 0:
        size = 2 * int(pixels) + 1
        mask = cv2.GaussianBlur(mask, (size, size), pixels / 2)
    return mask[..., None]


def resize_mask(mask, size):
    return cv2.resize(mask.astype(np.uint8) * 255, size, interpolation=cv2.INTER_LINEAR) > 127


def blend(destination, source, alpha):
    return (np.asarray(destination, np.float32) * (1 - alpha) + np.asarray(source, np.float32) * alpha)


def lock_personal_base(base, result, edit):
    """Keep the pose base's pixels outside the edit region so every garment render lines up exactly."""
    alpha = feather(resize_mask(edit, result.size), max(2, result.width // 300))
    return Image.fromarray(np.clip(blend(base, result, alpha), 0, 255).astype(np.uint8))


def hair_matte(personal, pose_base, hair):
    """Per-pixel hair opacity over the base tee, using the pose base as a clean plate.

    Outside the edit region the personal base *is* the pose base, so behind hair we know exactly
    what the tee looked like. Opacity is the colour difference from that plate, relative to the
    typical hair/plate difference, so wisps and gaps between curls blend correctly for any hair colour.
    """
    p, b = np.asarray(personal, np.float32), np.asarray(pose_base, np.float32)
    difference = np.linalg.norm(p - b, axis=2)
    core = cv2.erode(hair.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    reference = np.percentile(difference[core], 75) if core.sum() > 50 else 60.0
    alpha = np.clip(difference / max(reference, 12.0), 0, 1)
    region = _dilate(hair, max(2, hair.shape[1] // 200))
    return (cv2.GaussianBlur(alpha * region, (3, 3), 0))[..., None]


def compose(personal, garment_render, garment, hair, uncovered=None, pose_base=None, render_skin=None, base_plate=None):
    """Try-on = personal base, garment region from the render, the person's hair/covering on top.

    `hair`: hair/head covering that may lie over the garment (see covering_mask).
    `uncovered`: tee pixels (pose base or personal base) the new garment does not cover; filled from
    the personal base's surroundings.
    `pose_base` + `base_plate` (pose-base tee or background): clean plate for matting hair over the
    new garment, which keeps curls and wisps without a fringe.
    """
    size = personal.size
    feather_px = max(2, size[0] // 400)
    garment = resize_mask(garment, size)
    hair = resize_mask(hair, size)
    garment_alpha = feather(garment, feather_px)
    out = blend(personal, garment_render, garment_alpha)
    if uncovered is not None:
        uncovered = resize_mask(uncovered, size) & ~garment & ~hair
    if uncovered is not None and uncovered.any():
        # Fill from the personal base's own surroundings (its skin or background), so neither the
        # model's neck nor a differently lit render background shows through.
        filled = cv2.inpaint(np.clip(out, 0, 255).astype(np.uint8), _dilate(uncovered, 1).astype(np.uint8), 5, cv2.INPAINT_TELEA)
        alpha = feather(uncovered, feather_px)
        out = out * (1 - alpha) + filled.astype(np.float32) * alpha
    if hair.any():
        p = np.asarray(personal, np.float32)
        plate = resize_mask(base_plate, size) if base_plate is not None and pose_base is not None else np.zeros_like(hair)
        over = hair & plate
        if over.any():
            # Clean plate: behind hair, the pose base shows what was really there (tee or background)
            # and the render what should be there now. Swap only that contribution.
            alpha = hair_matte(personal, pose_base, over)
            replaced = p + (1 - alpha) * (np.asarray(garment_render, np.float32) - np.asarray(pose_base, np.float32))
            region = feather(_dilate(over, max(2, size[0] // 200)) & plate, feather_px)
            out = out * (1 - region) + replaced * region
        other = feather(hair & ~plate & garment, feather_px)
        out = out * (1 - other) + p * other
        # Hair that rested on the base tee can end a few pixels above a garment whose shoulder sits
        # lower. Close those thin gaps from the surrounding hair and fabric so the hair rests on it.
        k = max(3, size[0] // 90)
        closed = cv2.morphologyEx((garment | hair).astype(np.uint8), cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))) > 0
        gap = closed & ~garment & ~hair & _dilate(garment, k) & _dilate(hair, k)
        if gap.any():
            filled = cv2.inpaint(np.clip(out, 0, 255).astype(np.uint8), _dilate(gap, 1).astype(np.uint8), 5, cv2.INPAINT_TELEA)
            alpha = feather(gap, feather_px)
            out = out * (1 - alpha) + filled.astype(np.float32) * alpha
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def face_box(labels, margin=0.35):
    """Square box around the real head and hair, clamped inside the image (used for the close-up pass)."""
    head = head_mask(labels)
    if not head.any():
        raise CompositeError("We couldn't find the head in the generated look.")
    ys, xs = np.nonzero(head)
    h, w = labels.shape
    side = int(min(h, w, max(ys.max() - ys.min(), xs.max() - xs.min()) * (1 + margin)))
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    x0 = int(np.clip(cx - side / 2, 0, w - side))
    y0 = int(np.clip(cy - side / 2, 0, h - side))
    return x0, y0, x0 + side, y0 + side


def blend_face(image, redrawn, box, mask):
    """Blend a redrawn crop back into `image` at `box`, only inside `mask` (crop coordinates), fading
    to zero before the crop border so the crop edge never shows."""
    size = (box[2] - box[0], box[3] - box[1])
    region = np.asarray(image.crop(box), np.float32)
    source = np.asarray(redrawn.resize(size, Image.LANCZOS), np.float32)
    alpha = feather(resize_mask(mask, size), max(2, size[0] // 40))[..., 0]
    ramp = np.minimum.outer(np.minimum(np.arange(size[1]), np.arange(size[1])[::-1]),
                            np.minimum(np.arange(size[0]), np.arange(size[0])[::-1])) / max(1.0, 0.06 * size[0])
    alpha = (alpha * np.clip(ramp, 0, 1))[..., None]
    out = image.copy()
    out.paste(Image.fromarray(np.clip(region * (1 - alpha) + source * alpha, 0, 255).astype(np.uint8)), box[:2])
    return out
