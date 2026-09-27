"""Prepare private catalog thumbnails with ADC; preview by default, publish with --apply.

Example (after setting GOOGLE_APPLICATION_CREDENTIALS):
    python scripts/prepare-catalog-thumbnails.py --project PROJECT --bucket BUCKET --apply

Only active garments are considered. Original images, render cache keys, timestamps,
and printed tag destinations are never changed. Repeating a run reuses identical
content-addressed objects and skips unchanged documents.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sys
import warnings

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.worker.catalog import MAX_IMAGE_BYTES, catalog_id, firebase_clients

MAX_GARMENTS = 100
MAX_PIXELS = 40_000_000
MAX_THUMBNAIL_BYTES = 1024 * 1024
THUMBNAIL_EDGE = 512


def thumbnail_jpeg(data):
    """Orient, fit and flatten on white, then encode without source metadata."""
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError("Source image must contain 1 byte to 20 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if (source.format not in {"PNG", "JPEG", "WEBP"}
                        or source.width * source.height > MAX_PIXELS
                        or getattr(source, "n_frames", 1) != 1):
                    raise ValueError("Source must be a still PNG, JPEG or WebP of at most 40 megapixels.")
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=82, optimize=True, progressive=True)
                result = output.getvalue()
                if len(result) > MAX_THUMBNAIL_BYTES:
                    raise ValueError("Encoded thumbnail exceeds 1 MiB.")
                return result, image.size
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Source image could not be safely decoded.") from None


def bounded_download(blob, limit):
    """Bound reads by known object size and pin the exact generation inspected."""
    if blob is None or blob.size is None or not 0 < blob.size <= limit or blob.generation is None:
        raise ValueError("A catalog object is missing, empty, oversized or lacks a generation.")
    data = blob.download_as_bytes(start=0, end=blob.size - 1, raw_download=True,
                                  if_generation_match=blob.generation, timeout=60)
    if len(data) != blob.size:
        raise ValueError("A catalog object changed size or could not be downloaded completely.")
    return data


def prepare_thumbnails(db, bucket, *, apply=False, expected_count=None):
    from google.cloud.firestore_v1.base_query import FieldFilter

    # One extra result detects an oversized catalog without an unbounded read.
    snapshots = list(db.collection("garments").where(filter=FieldFilter("active", "==", True))
                     .limit(MAX_GARMENTS + 1).stream(timeout=60))
    if len(snapshots) > MAX_GARMENTS:
        raise ValueError(f"Refusing more than {MAX_GARMENTS} active garments in one run.")
    if expected_count is not None and len(snapshots) != expected_count:
        raise ValueError(f"Expected {expected_count} active garments, found {len(snapshots)}.")

    plans = []
    # Complete validation before writing any object or document.
    for snapshot in sorted(snapshots, key=lambda item: item.id):
        garment_id = catalog_id(snapshot.id)
        garment = snapshot.to_dict()
        source_path = f"garments/{garment_id}/reference.png"
        if garment.get("active") is not True or garment.get("imagePath") != source_path:
            raise ValueError(f"Garment {garment_id} must have its canonical active reference image.")
        source = bounded_download(bucket.get_blob(source_path, timeout=60), MAX_IMAGE_BYTES)
        data, dimensions = thumbnail_jpeg(source)
        digest = hashlib.sha256(data).hexdigest()
        path = f"garments/{garment_id}/thumbnail-{digest}.jpg"
        existing = bucket.get_blob(path, timeout=60)
        if existing is not None:
            if (existing.content_type != "image/jpeg"
                    or (existing.metadata or {}).get("firebaseStorageDownloadTokens")
                    or existing.size != len(data)
                    or not hmac.compare_digest(hashlib.sha256(bounded_download(existing, MAX_THUMBNAIL_BYTES)).hexdigest(), digest)):
                raise ValueError(f"Existing thumbnail for {garment_id} is not identical and private; nothing was overwritten.")
        plans.append({"snapshot": snapshot, "data": data, "missing": existing is None,
                      "changed": garment.get("thumbnailPath") != path,
                      "report": {"garmentId": garment_id, "thumbnailPath": path,
                                 "sourceBytes": len(source), "thumbnailBytes": len(data),
                                 "dimensions": list(dimensions)}})

    if apply:
        for plan in plans:
            if plan["missing"]:
                blob = bucket.blob(plan["report"]["thumbnailPath"])
                blob.cache_control = "private, max-age=86400"
                blob.metadata = {}  # Never create a public Firebase download token.
                blob.upload_from_string(plan["data"], content_type="image/jpeg",
                                        if_generation_match=0, timeout=60)
        changed = [plan for plan in plans if plan["changed"]]
        if changed:
            batch = db.batch()
            for plan in changed:
                # Only this field changes. The timestamp precondition rejects stale
                # sources, deactivation and deletion since preflight; retry safely.
                snapshot = plan["snapshot"]
                batch.update(snapshot.reference, {"thumbnailPath": plan["report"]["thumbnailPath"]},
                             option=db.write_option(last_update_time=snapshot.update_time))
            batch.commit(timeout=60)

    return {"mode": "published" if apply else "dry-run", "activeGarments": len(plans),
            "objectsToUpload" if not apply else "uploadedObjects": sum(plan["missing"] for plan in plans),
            "documentsToUpdate" if not apply else "updatedDocuments": sum(plan["changed"] for plan in plans),
            "sourceBytes": sum(plan["report"]["sourceBytes"] for plan in plans),
            "thumbnailBytes": sum(plan["report"]["thumbnailBytes"] for plan in plans),
            "garments": [plan["report"] for plan in plans]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("FIREBASE_PROJECT_ID"))
    parser.add_argument("--bucket", default=os.environ.get("FIREBASE_STORAGE_BUCKET"))
    parser.add_argument("--expected-count", type=int, help="Refuse a catalog count different from this value.")
    parser.add_argument("--apply", action="store_true", help="Publish thumbnails and merge only thumbnailPath.")
    args = parser.parse_args(argv)
    if not args.project or not args.bucket:
        parser.error("Set FIREBASE_PROJECT_ID and FIREBASE_STORAGE_BUCKET, or pass --project and --bucket.")
    if args.expected_count is not None and not 0 <= args.expected_count <= MAX_GARMENTS:
        parser.error(f"--expected-count must be between 0 and {MAX_GARMENTS}.")
    try:
        with firebase_clients(args.project, args.bucket) as (db, bucket):
            result = prepare_thumbnails(db, bucket, apply=args.apply, expected_count=args.expected_count)
        print(json.dumps({"project": args.project, "bucket": args.bucket, **result}, indent=2))
    except (ValueError, argparse.ArgumentTypeError) as error:
        parser.exit(1, f"Thumbnail preparation failed: {error}\n")
    except Exception as error:
        # Avoid exposing credentials, request data or private image bytes in errors.
        parser.exit(1, f"Thumbnail preparation failed ({type(error).__name__}). Check ADC and project permissions.\n")


if __name__ == "__main__":
    main()
