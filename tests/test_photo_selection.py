"""CPU-only selector checks using synthetic pixels and mocked face boxes."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from services.worker import photo_selection as selector


class PhotoSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.paths = []
        for index in range(8):
            pixels = np.random.default_rng(index).integers(50, 210, (512, 512, 3), dtype=np.uint8)
            path = self.directory / f"synthetic_{index}.png"
            Image.fromarray(pixels).save(path)
            self.paths.append(path)

    def run_selector(self, boxes=None):
        with patch.object(selector, "_detect_faces", return_value=boxes or [(128, 128, 256, 256)]):
            return selector.select_best_photos(self.paths)

    def test_returns_all_eight_exactly_five_selected_and_json_safe(self):
        records = self.run_selector()
        self.assertEqual([item["index"] for item in records], list(range(8)))
        self.assertEqual(sum(item["selected"] for item in records), 5)
        self.assertTrue(all(isinstance(item["score"], float) and item["reason"] for item in records))
        json.dumps(records, allow_nan=False)
        self.assertEqual(records, self.run_selector())

    def test_requires_exactly_eight_inputs(self):
        with self.assertRaisesRegex(ValueError, "exactly 8.*received 7"):
            selector.select_best_photos(self.paths[:7])

    def test_quality_ties_preserve_input_order(self):
        records = self.run_selector()
        self.assertEqual(len({item["score"] for item in records}), 1)
        self.assertEqual([item["index"] for item in records if item["selected"]], [0, 1, 2, 3, 4])

    def test_corrupt_tiny_and_multiple_face_inputs_remain_visibly_rejected(self):
        self.paths[0].write_bytes(b"not an image")
        Image.new("RGB", (100, 200), "gray").save(self.paths[1])
        calls = [[(20, 20, 150, 150), (250, 250, 150, 150)]] + [[(128, 128, 256, 256)]] * 5
        with patch.object(selector, "_detect_faces", side_effect=calls):
            records = selector.select_best_photos(self.paths)
        self.assertEqual(sum(item["selected"] for item in records), 5)
        self.assertIn("Cannot read", records[0]["reason"])
        self.assertIn("too small", records[1]["reason"])
        self.assertIn("2 faces", records[2]["reason"])
        self.assertTrue(all(not item["selected"] for item in records[:3]))

    def test_missing_face_and_small_face_have_actionable_reasons(self):
        calls = [[], [(128, 128, 80, 80)]] + [[(128, 128, 256, 256)]] * 6
        with patch.object(selector, "_detect_faces", side_effect=calls):
            records = selector.select_best_photos(self.paths)
        self.assertIn("No clear frontal face", records[0]["reason"])
        self.assertIn("96 pixels", records[1]["reason"])
        self.assertFalse(records[0]["selected"])
        self.assertFalse(records[1]["selected"])

    def test_blur_and_extreme_exposure_are_rejected(self):
        for index, value in ((0, 128), (1, 255)):
            with Image.open(self.paths[index]) as image:
                pixels = np.array(image)
            pixels[128:384, 128:384] = value
            Image.fromarray(pixels).save(self.paths[index])
        records = self.run_selector()
        self.assertIn("blurry", records[0]["reason"])
        self.assertIn("extreme exposure", records[1]["reason"])
        self.assertFalse(records[0]["selected"])
        self.assertFalse(records[1]["selected"])

    def test_duplicate_heavy_batch_fails_before_training(self):
        repeated = self.paths[:4] * 2
        with patch.object(selector, "_detect_faces", return_value=[(128, 128, 256, 256)]):
            with self.assertRaisesRegex(ValueError, "Only 4 distinct usable.*Replace at least 1.*Duplicate"):
                selector.select_best_photos(repeated)

    def test_near_duplicate_with_brightness_edit_is_not_selected_twice(self):
        with Image.open(self.paths[0]) as image:
            brighter = np.asarray(image).astype(np.int16) + 3
        Image.fromarray(brighter.astype(np.uint8)).save(self.paths[1])
        records = self.run_selector()
        self.assertFalse(records[1]["selected"])
        self.assertEqual(records[1]["metrics"]["duplicate_of"], 0)
        self.assertIn("near duplicate", records[1]["reason"])
        self.assertEqual(sum(item["selected"] for item in records), 5)

    def test_insufficient_faces_names_the_inputs_to_replace(self):
        calls = [[(128, 128, 256, 256)]] * 4 + [[]] * 4
        with patch.object(selector, "_detect_faces", side_effect=calls):
            with self.assertRaisesRegex(ValueError, "Only 4 distinct usable.*Photo 5: No clear.*Photo 8:"):
                selector.select_best_photos(self.paths)

    def test_variety_can_prefer_a_slightly_smaller_clear_face(self):
        # The last photo has a slightly smaller face and different framing.
        calls = [[(128, 128, 256, 256)]] * 7 + [[(10, 128, 240, 240)]]
        with patch.object(selector, "_detect_faces", side_effect=calls):
            records = selector.select_best_photos(self.paths)
        self.assertLess(records[7]["score"], records[0]["score"])
        self.assertTrue(records[7]["selected"])
        self.assertIn("variety", records[7]["reason"])
        self.assertEqual(sum(item["selected"] for item in records), 5)

    def test_exif_orientation_is_applied_before_analysis(self):
        pixels = np.random.default_rng(42).integers(50, 210, (600, 400, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
        exif = Image.Exif()
        exif[274] = 6
        image.save(self.paths[0], exif=exif)
        records = self.run_selector()
        self.assertEqual((records[0]["metrics"]["width"], records[0]["metrics"]["height"]), (600, 400))


if __name__ == "__main__":
    unittest.main()
