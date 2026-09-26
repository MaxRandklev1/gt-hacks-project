"""Operator-only Firebase catalog and browser-origin setup using ADC."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import io
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from PIL import Image, ImageOps

try:
    from .comfy import safe_id
except ImportError:
    from comfy import safe_id


MAX_IMAGE_BYTES = 20 * 1024 * 1024


def catalog_id(value):
    try:
        return safe_id(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Use 1–128 letters, digits, hyphens or underscores, starting with a letter or digit.") from None


def normalize_origin(value):
    """A single HTTPS origin, or HTTP on an explicit local loopback host."""
    message = "Use an HTTPS origin or HTTP localhost, without credentials, paths, queries, fragments or wildcards."
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 32 for char in value):
        raise argparse.ArgumentTypeError(message)
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query
                or parsed.fragment or "?" in value or "#" in value or "\\" in value
                or "*" in host or "%" in host or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
            host = address.compressed
            loopback = host in {"127.0.0.1", "::1"}
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
            if (len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                       for label in host.split("."))):
                raise ValueError
            loopback = host == "localhost"
        if parsed.scheme == "http" and not loopback:
            raise ValueError
        # URL serialization omits the scheme's default port.
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, UnicodeError):
        raise argparse.ArgumentTypeError(message) from None


def normalized_png(path):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError("Each catalog image must be a local nonempty file no larger than 20 MiB.")
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("A catalog image changed or exceeds 20 MiB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("A catalog image exceeds 40 megapixels.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
            image.info.clear()
            output = io.BytesIO()
            image.save(output, format="PNG")
            result = output.getvalue()
            if len(result) > MAX_IMAGE_BYTES:
                raise ValueError("A normalized catalog PNG exceeds the worker's 20 MiB input limit.")
            return result, image.size
    except (OSError, Image.DecompressionBombError):
        raise ValueError("A catalog image could not be decoded.") from None


@contextmanager
def firebase_clients(project, bucket_name):
    # A named app neither reconfigures nor collides with an existing worker app.
    import firebase_admin
    from firebase_admin import firestore, storage
    app = firebase_admin.initialize_app(options={"projectId": project, "storageBucket": bucket_name},
                                        name="gt-hacks-catalog-" + uuid.uuid4().hex)
    try:
        yield firestore.client(app=app), storage.bucket(app=app)
    finally:
        firebase_admin.delete_app(app)


def seed_garment(db, bucket, *, garment_id, name, brand, reference, base, description=""):
    garment_id = catalog_id(garment_id)
    name, brand, description = name.strip(), brand.strip(), description.strip()
    if not name or len(name) > 160 or not brand or len(brand) > 160 or len(description) > 2000:
        raise ValueError("Name and brand need 1–160 characters; description may contain up to 2000.")
    # Decode and validate both inputs before writing either object.
    images = {"reference": normalized_png(reference), "base": normalized_png(base)}
    prefix = f"garments/{garment_id}"
    missing = []
    for role, (data, _) in images.items():
        path = f"{prefix}/{role}.png"
        current = bucket.get_blob(path)
        if current is not None:
            different = f"Catalog image {path} already exists with different bytes. Use another garment ID or an explicit admin update; no object was overwritten."
            if current.size != len(data) or current.generation is None:
                raise ValueError(different)
            # The size must match a bounded local PNG before downloading. Pin the
            # exact generation and byte range; never trust an object's hash metadata.
            existing = current.download_as_bytes(if_generation_match=current.generation, raw_download=True,
                                                   start=0, end=len(data) - 1)
            if len(existing) != len(data) or not hmac.compare_digest(hashlib.sha256(existing).digest(), hashlib.sha256(data).digest()):
                raise ValueError(different)
        else:
            missing.append((path, data))
    for path, data in missing:
        blob = bucket.blob(path)
        blob.cache_control = "private, max-age=300"
        blob.metadata = {}  # Never create a public Firebase download token.
        blob.upload_from_string(data, content_type="image/png", if_generation_match=0)
    document = {"name": name, "brand": brand, "description": description, "active": True,
                "imagePath": prefix + "/reference.png", "baseImagePath": prefix + "/base.png",
                "updatedAt": datetime.now(timezone.utc)}
    # Merge into only this ID. Other garments and unrelated fields remain untouched.
    db.collection("garments").document(garment_id).set(document, merge=True)
    return {"garmentId": garment_id, "active": True, "imagePath": document["imagePath"],
            "baseImagePath": document["baseImagePath"],
            "dimensions": {role: list(value[1]) for role, value in images.items()}}


def configure_cors(bucket, origins):
    origins = list(dict.fromkeys(normalize_origin(value) for value in origins))
    if not origins:
        raise ValueError("Provide at least one browser origin.")
    bucket.reload()
    generation = bucket.metageneration
    bucket.cors = [{"origin": origins, "method": ["GET", "HEAD"],
                    "responseHeader": ["Authorization", "Content-Type", "Content-Length", "Content-Disposition",
                                       "ETag", "Range", "X-Firebase-AppCheck", "X-Firebase-GMPID", "X-Firebase-Storage-Version"],
                    "maxAgeSeconds": 3600}]
    # Replace this bucket's CORS allowlist deliberately; no unrelated bucket fields change.
    bucket.patch(if_metageneration_match=generation)
    return {"origins": origins, "methods": ["GET", "HEAD"], "maxAgeSeconds": 3600}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("FIREBASE_PROJECT_ID"))
    parser.add_argument("--bucket", default=os.environ.get("FIREBASE_STORAGE_BUCKET"))
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed-garment", help="Publish the named garment; create missing images or verify identical existing bytes.")
    seed.add_argument("--id", type=catalog_id, required=True)
    seed.add_argument("--name", required=True)
    seed.add_argument("--brand", required=True)
    seed.add_argument("--description", default="")
    seed.add_argument("--reference", type=Path, required=True)
    seed.add_argument("--base", type=Path, required=True)
    cors = sub.add_parser("configure-cors", help="Replace the bucket's read-only browser CORS allowlist.")
    cors.add_argument("--origin", type=normalize_origin, action="append", required=True)
    args = parser.parse_args(argv)
    if not args.project or not args.bucket:
        parser.error("Set FIREBASE_PROJECT_ID and FIREBASE_STORAGE_BUCKET, or pass --project and --bucket before the command.")
    try:
        with firebase_clients(args.project, args.bucket) as (db, bucket):
            if args.command == "seed-garment":
                result = seed_garment(db, bucket, garment_id=args.id, name=args.name, brand=args.brand,
                                      description=args.description, reference=args.reference, base=args.base)
            else:
                result = configure_cors(bucket, args.origin)
        print(json.dumps({"project": args.project, "bucket": args.bucket, **result}, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Catalog setup failed: {error}\n")
    except Exception as error:
        # SDK exceptions can contain credential paths or request data; print only their class.
        parser.exit(1, f"Catalog setup failed ({type(error).__name__}). Check ADC, project/bucket, billing and admin permissions.\n")


if __name__ == "__main__":
    main()
