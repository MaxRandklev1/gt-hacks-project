import argparse
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from services.worker.catalog import catalog_id, configure_cors, normalize_origin, normalized_png, seed_garment


class CatalogTests(unittest.TestCase):
    def test_ids_cannot_escape_catalog_collection(self):
        self.assertEqual(catalog_id("demo-shirt_2"), "demo-shirt_2")
        for value in ("", "../alice", "users/alice", "a/b", "a\\b", "-shirt", "x" * 129):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                catalog_id(value)

    def test_origins_are_normalized_and_local_http_is_explicit(self):
        cases = {"HTTPS://Example.COM:443/": "https://example.com", "https://app.example:8443": "https://app.example:8443",
                 "http://LOCALHOST:5173/": "http://localhost:5173", "http://127.0.0.1:80": "http://127.0.0.1",
                 "http://[::1]:5173": "http://[::1]:5173"}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_origin(value), expected)

    def test_origins_reject_wildcards_credentials_and_non_origins(self):
        for value in ("*", "https://*.example.com", "http://example.com", "http://localhost.evil.test",
                      "https://user:pass@example.com", "https://@example.com", "https://app.test/path",
                      "https://app.test/?token=secret", "https://app.test/#fragment", "https://app.test?",
                      "https://app.test#", "file:///tmp", "https://bad_host", "https://app.test:0",
                      "https://app.test:65536", " https://app.test", "https://app.test\n", "https://evil\\host"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                normalize_origin(value)

    def test_normalized_png_strips_text_exif_and_preserves_oriented_pixels(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.png"
            metadata = PngInfo()
            metadata.add_text("prompt", "private sample")
            exif = Image.Exif()
            exif[274] = 6
            exif[315] = "private author"
            Image.new("RGB", (8, 4), "red").save(path, pnginfo=metadata, exif=exif)
            result, dimensions = normalized_png(path)
            self.assertEqual(dimensions, (4, 8))
            with Image.open(io.BytesIO(result)) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.info, {})
                self.assertFalse(image.getexif())

    def test_seed_only_writes_selected_id_and_two_canonical_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "image.png"
            Image.new("RGB", (8, 8), "blue").save(path)
            db, bucket = Mock(), Mock()
            bucket.get_blob.return_value = None
            result = seed_garment(db, bucket, garment_id="demo-shirt", name="Demo shirt", brand="Demo",
                                  reference=path, base=path)
            db.collection.assert_called_once_with("garments")
            db.collection.return_value.document.assert_called_once_with("demo-shirt")
            write = db.collection.return_value.document.return_value.set
            self.assertTrue(write.call_args.kwargs["merge"])
            self.assertEqual(write.call_args.args[0]["imagePath"], "garments/demo-shirt/reference.png")
            self.assertEqual([call.args[0] for call in bucket.blob.call_args_list],
                             ["garments/demo-shirt/reference.png", "garments/demo-shirt/base.png"])
            for call in bucket.blob.return_value.upload_from_string.call_args_list:
                self.assertEqual(call.kwargs, {"content_type": "image/png", "if_generation_match": 0})
            self.assertEqual(result["dimensions"]["base"], [8, 8])

    def test_invalid_second_image_prevents_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.png"
            Image.new("RGB", (8, 8)).save(path)
            db, bucket = Mock(), Mock()
            with self.assertRaises(ValueError):
                seed_garment(db, bucket, garment_id="shirt", name="Shirt", brand="Test",
                             reference=path, base=Path(temp) / "missing.png")
            bucket.blob.assert_not_called()
            db.collection.assert_not_called()

    def test_identical_existing_images_are_verified_without_reupload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.png"
            Image.new("RGB", (8, 8), "green").save(path)
            data, _ = normalized_png(path)
            db, bucket = Mock(), Mock()
            existing = Mock(size=len(data), generation=42)
            existing.download_as_bytes.return_value = data
            bucket.get_blob.return_value = existing
            seed_garment(db, bucket, garment_id="shirt", name="Shirt", brand="Test", reference=path, base=path)
            self.assertEqual(existing.download_as_bytes.call_count, 2)
            for call in existing.download_as_bytes.call_args_list:
                self.assertEqual(call.kwargs, {"if_generation_match": 42, "raw_download": True,
                                               "start": 0, "end": len(data) - 1})
            bucket.blob.assert_not_called()
            db.collection.return_value.document.return_value.set.assert_called_once()

    def test_different_existing_bytes_fail_without_upload_or_document_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.png"
            Image.new("RGB", (8, 8), "green").save(path)
            data, _ = normalized_png(path)
            db, bucket = Mock(), Mock()
            existing = Mock(size=len(data), generation=42)
            existing.download_as_bytes.return_value = b"x" * len(data)
            # The first object is missing; the second mismatch still prevents all uploads.
            bucket.get_blob.side_effect = [None, existing]
            with self.assertRaisesRegex(ValueError, "Use another garment ID"):
                seed_garment(db, bucket, garment_id="shirt", name="Shirt", brand="Test", reference=path, base=path)
            bucket.blob.assert_not_called()
            db.collection.assert_not_called()

    def test_cors_replaces_only_allowlist_with_generation_guard(self):
        bucket = Mock(metageneration=7)
        result = configure_cors(bucket, ["https://EXAMPLE.test:443/", "https://example.test", "http://localhost:5173"])
        bucket.reload.assert_called_once_with()
        bucket.patch.assert_called_once_with(if_metageneration_match=7)
        self.assertEqual(result["origins"], ["https://example.test", "http://localhost:5173"])
        self.assertEqual(bucket.cors[0]["method"], ["GET", "HEAD"])
        self.assertIn("Authorization", bucket.cors[0]["responseHeader"])
        self.assertNotIn("*", bucket.cors[0]["origin"])


if __name__ == "__main__":
    unittest.main()
