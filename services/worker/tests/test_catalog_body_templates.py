"""Body-template publication tests using an in-memory storage/database boundary."""
import argparse
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from services.worker.body_templates import BODY_TEMPLATE_IDS
from services.worker.catalog import normalized_png, seed_body_templates


class Blob:
    def __init__(self, bucket, path):
        self.bucket, self.path = bucket, path
        self.size = len(bucket.objects[path]) if path in bucket.objects else None
        self.generation = 7 if path in bucket.objects else None
        self.metadata = None
        self.cache_control = None

    def download_as_bytes(self, **kwargs):
        self.bucket.downloads.append((self.path, kwargs))
        expected = {"if_generation_match": 7, "raw_download": True, "start": 0,
                    "end": len(self.bucket.objects[self.path]) - 1}
        if kwargs != expected:
            raise AssertionError("Existing bytes must be bounded and generation-pinned")
        return self.bucket.objects[self.path]

    def upload_from_string(self, data, **kwargs):
        if kwargs != {"content_type": "image/png", "if_generation_match": 0}:
            raise AssertionError("Only create-only PNG uploads are allowed")
        if self.path in self.bucket.objects or self.path == self.bucket.fail_path:
            raise RuntimeError("Storage precondition failed")
        self.bucket.objects[self.path] = data
        self.bucket.uploads.append((self.path, kwargs, self.metadata, self.cache_control))


class Bucket:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.uploads, self.downloads, self.reads = [], [], []
        self.fail_path = None

    def get_blob(self, path):
        self.reads.append(path)
        return Blob(self, path) if path in self.objects else None

    def blob(self, path):
        return Blob(self, path)


class Snapshot:
    def __init__(self, value):
        self.value = deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self.value)


class Reference:
    def __init__(self, db, key):
        self.db, self.key = db, key

    def get(self):
        self.db.reads.append(self.key)
        return Snapshot(self.db.documents.get(self.key))


class Batch:
    def __init__(self, db):
        self.db, self.pending = db, []

    def update(self, reference, value):
        self.pending.append((reference.key, deepcopy(value)))

    def commit(self):
        if any(key not in self.db.documents for key, _ in self.pending):
            raise RuntimeError("Cannot recreate a deleted catalog document")
        for key, value in self.pending:
            self.db.documents[key].update(deepcopy(value))
        self.db.commits.append(self.pending)


class Database:
    def __init__(self, documents):
        self.documents = deepcopy(documents)
        self.reads, self.commits = [], []

    def collection(self, name):
        if name != "garments":
            raise AssertionError("Only the garment catalog may change")
        return self

    def document(self, key):
        return Reference(self, key)

    def batch(self):
        return Batch(self)


class BodyTemplatePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        metadata = PngInfo()
        metadata.add_text("prompt", "should never be published")
        for index in range(1, 6):
            Image.new("RGB", (8, 8), (index * 35, 10, 100)).save(
                self.directory / f"Pose1_Weight{index}.png", pnginfo=metadata)
        self.images = {key: normalized_png(self.directory / f"Pose1_Weight{index}.png")[0]
                       for index, key in enumerate(BODY_TEMPLATE_IDS, 1)}
        self.original = {key: {"name": key, "active": True, "updatedAt": "stable-render-cache-version",
                               "baseImagePath": f"garments/{key}/base.png",
                               "imagePath": f"garments/{key}/reference.png", "unrelated": {"keep": True}}
                         for key in ("shirt", "other", "untouched")}
        self.db = Database(self.original)
        self.bucket = Bucket({f"garments/{key}/base.png": self.images["weight-3"] for key in self.original})

    def seed(self, ids=("shirt",)):
        return seed_body_templates(self.db, self.bucket, directory=self.directory, garment_ids=ids)

    def assert_no_writes(self):
        self.assertEqual(self.bucket.uploads, [])
        self.assertEqual(self.db.commits, [])
        self.assertEqual(self.db.documents, self.original)

    def test_matching_middle_reuses_legacy_and_preserves_all_existing_fields(self):
        result = self.seed(("shirt", "shirt", "other"))
        self.assertEqual(result["uploadedImages"], 8)
        self.assertEqual(result["updatedGarments"], 2)
        self.assertEqual(result["dimensions"], [8, 8])
        self.assertEqual(self.db.reads, ["shirt", "other"])
        self.assertEqual(len(self.db.commits), 1)
        for key in ("shirt", "other"):
            actual = self.db.documents[key]
            self.assertEqual({field: actual[field] for field in self.original[key]}, self.original[key])
            mapping = actual["bodyBaseImagePaths"]
            self.assertEqual(set(mapping), set(BODY_TEMPLATE_IDS))
            self.assertEqual(mapping["weight-3"], f"garments/{key}/base.png")
            for template in BODY_TEMPLATE_IDS:
                self.assertEqual(self.bucket.objects[mapping[template]], self.images[template])
        for _, value in self.db.commits[0]:
            self.assertEqual(set(value), {"bodyBaseImagePaths", "bodyTemplatesUpdatedAt"})
        for path, _, metadata, cache_control in self.bucket.uploads:
            self.assertIn("/body-bases/", path)
            self.assertEqual(metadata, {})
            self.assertEqual(cache_control, "private, max-age=300")
        self.assertEqual(self.db.documents["untouched"], self.original["untouched"])

    def test_identical_repeat_verifies_bytes_but_writes_nothing(self):
        self.seed()
        before = deepcopy(self.db.documents)
        downloads = len(self.bucket.downloads)
        self.bucket.uploads.clear(); self.db.commits.clear()
        result = self.seed()
        self.assertEqual(result["uploadedImages"], 0)
        self.assertEqual(result["updatedGarments"], 0)
        self.assertFalse(result["garments"][0]["updated"])
        self.assertEqual(self.bucket.uploads, [])
        self.assertEqual(self.db.commits, [])
        self.assertEqual(self.db.documents, before)
        self.assertEqual(len(self.bucket.downloads) - downloads, 5)

    def test_renamed_male_sources_preserve_the_same_idempotent_publication(self):
        self.seed()
        before = deepcopy(self.db.documents)
        self.bucket.uploads.clear(); self.db.commits.clear()
        for number in range(1, 6):
            (self.directory / f"Pose1_Weight{number}.png").rename(
                self.directory / f"Pose1_Weight{number}_Male.png")
            # A separate template set must not affect resolution or the published hashes.
            Image.new("RGB", (8, 8), "yellow").save(self.directory / f"Pose1_Weight{number}_Female.png")
        result = self.seed()
        self.assertEqual(result["uploadedImages"], 0)
        self.assertEqual(result["updatedGarments"], 0)
        self.assertEqual(self.bucket.uploads, [])
        self.assertEqual(self.db.commits, [])
        self.assertEqual(self.db.documents, before)

    def test_original_filename_takes_precedence_over_different_male_copy(self):
        for number in range(1, 6):
            Image.new("RGB", (8, 8), "yellow").save(self.directory / f"Pose1_Weight{number}_Male.png")
        result = self.seed()
        for template, path in result["garments"][0]["bodyBaseImagePaths"].items():
            self.assertEqual(self.bucket.objects[path], self.images[template])

    def test_female_source_is_never_a_fallback_for_a_missing_original_template(self):
        (self.directory / "Pose1_Weight5.png").rename(self.directory / "Pose1_Weight5_Female.png")
        with self.assertRaises(ValueError):
            self.seed()
        self.assert_no_writes()
        self.assertEqual(self.db.reads, [])
        self.assertEqual(self.bucket.reads, [])

    def test_different_legacy_base_gets_separate_middle_without_overwriting_legacy(self):
        legacy = self.images["weight-1"]
        self.bucket.objects["garments/shirt/base.png"] = legacy
        result = self.seed()
        self.assertEqual(result["uploadedImages"], 5)
        self.assertEqual(result["garments"][0]["bodyBaseImagePaths"]["weight-3"],
                         "garments/shirt/body-bases/weight-3.png")
        self.assertEqual(self.bucket.objects["garments/shirt/base.png"], legacy)

    def test_missing_or_mismatched_last_local_image_prevents_even_catalog_reads(self):
        last = self.directory / "Pose1_Weight5.png"
        last.unlink()
        with self.assertRaises(ValueError):
            self.seed()
        self.assert_no_writes()
        self.assertEqual(self.db.reads, [])
        self.assertEqual(self.bucket.reads, [])
        Image.new("RGB", (8, 9)).save(last)
        with self.assertRaisesRegex(ValueError, "same image dimensions"):
            self.seed()
        self.assert_no_writes()
        self.assertEqual(self.db.reads, [])

    def test_later_garment_byte_conflict_prevents_every_write(self):
        conflict = "garments/other/body-bases/weight-5.png"
        self.bucket.objects[conflict] = b"x" * len(self.images["weight-5"])
        with self.assertRaisesRegex(ValueError, "different bytes"):
            self.seed(("shirt", "other"))
        self.assert_no_writes()
        self.assertEqual(self.bucket.objects[conflict], b"x" * len(self.images["weight-5"]))

    def test_missing_later_garment_prevents_earlier_uploads(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.seed(("shirt", "missing"))
        self.assert_no_writes()

    def test_missing_legacy_or_foreign_paths_cannot_publish(self):
        self.bucket.objects.pop("garments/shirt/base.png")
        with self.assertRaisesRegex(ValueError, "missing its original"):
            self.seed()
        self.assert_no_writes()
        self.db.documents["shirt"]["imagePath"] = "users/alice/private.png"
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            self.seed()
        self.assertEqual(self.bucket.uploads, [])
        self.assertEqual(self.db.commits, [])

    def test_unsafe_or_empty_explicit_ids_are_rejected_before_reads(self):
        for ids in ((), ("../shirt",), ("users/alice",), tuple(f"shirt-{i}" for i in range(101))):
            with self.subTest(ids=ids), self.assertRaises((ValueError, argparse.ArgumentTypeError)):
                self.seed(ids)
        self.assert_no_writes()
        self.assertEqual(self.db.reads, [])

    def test_storage_failure_never_publishes_incomplete_map_and_retry_is_safe(self):
        self.bucket.fail_path = "garments/shirt/body-bases/weight-2.png"
        with self.assertRaises(RuntimeError):
            self.seed()
        self.assertEqual(len(self.bucket.uploads), 1)
        self.assertEqual(self.db.commits, [])
        self.assertEqual(self.db.documents, self.original)
        self.bucket.fail_path = None
        result = self.seed()
        self.assertEqual(result["uploadedImages"], 3)
        self.assertEqual(result["updatedGarments"], 1)

    def test_map_replacement_removes_obsolete_keys_without_touching_other_fields(self):
        self.db.documents["shirt"]["bodyBaseImagePaths"] = {"obsolete": "stale/path.png"}
        self.seed()
        self.assertEqual(set(self.db.documents["shirt"]["bodyBaseImagePaths"]), set(BODY_TEMPLATE_IDS))
        self.assertEqual(self.db.documents["shirt"]["updatedAt"], self.original["shirt"]["updatedAt"])


if __name__ == "__main__":
    unittest.main()
