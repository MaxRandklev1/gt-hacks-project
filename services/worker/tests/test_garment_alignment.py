from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from services.worker.compose import alignment_score, base_regions, garment_mask
from services.worker.parsing import LABELS
from services.worker.personal import GARMENT_MASK_VERSION
from services.worker.worker import Worker


L = {name: i for i, name in enumerate(LABELS)}


def pose():
    labels = np.zeros((200, 200), np.uint8)
    labels[20:60, 80:120] = L["face"]  # Includes the neck, as the real parser does.
    labels[60:120, 50:150] = L["upper_clothes"]
    labels[120:200, 65:135] = L["pants"]
    return labels


def score(a, b):
    return alignment_score(a, b, garment_occlusion=garment_mask(b, base_regions(a)))


class GarmentAlignmentTests(unittest.TestCase):
    def test_high_collar_and_longer_hem_are_valid_coverage(self):
        a, b = pose(), pose()
        b[48:60, 80:120] = L["upper_clothes"]
        b[120:145, 65:135] = L["upper_clothes"]
        self.assertLess(alignment_score(a, b), 0.85)
        self.assertEqual(score(a, b), 1.0)

    def test_face_and_lower_body_must_each_align_without_averaging(self):
        a = pose()
        cases = []
        for region, label in [((20, 60, 80, 120), "face"), ((120, 200, 65, 135), "pants")]:
            y0, y1, x0, x1 = region
            for shift in (12, -12):
                b = pose()
                b[y0:y1, x0:x1] = 0
                b[y0:y1, x0 + shift:x1 + shift] = L[label]
                cases.append((label + str(shift), b))
            b = pose()
            b[y0:y1, x0 - 12:x1 + 12] = L[label]
            cases.append((label + " enlarged", b))
        for name, b in cases:
            with self.subTest(name=name):
                self.assertLess(score(a, b), 0.85)

    def test_missing_anchors_and_excessive_occlusion_fail(self):
        a = pose()
        for label in ("face", "pants"):
            b = pose()
            b[b == L[label]] = L["upper_clothes"]
            self.assertEqual(score(a, b), 0)
        for label in ("face", "pants"):
            b = pose()
            if label == "face":
                b[39:60, 80:120] = L["upper_clothes"]  # More than half covered.
            else:
                b[120:161, 65:135] = L["upper_clothes"]
            self.assertEqual(score(a, b), 0)
        empty = np.zeros_like(a)
        self.assertEqual(score(empty, empty), 0)
        self.assertEqual(score(empty, a), 0)
        self.assertEqual(score(a, empty), 0)

    def test_exactly_half_visible_is_the_bound_and_not_a_new_threshold(self):
        a, b = pose(), pose()
        b[40:60, 80:120] = L["upper_clothes"]
        b[120:160, 65:135] = L["upper_clothes"]
        self.assertEqual(score(a, b), 1.0)
        b[39:40, 80:120] = L["upper_clothes"]
        self.assertEqual(score(a, b), 0)

    def test_malformed_occlusion_cannot_erase_visible_movement(self):
        a, b = pose(), pose()
        b[20:60, 80:120] = 0
        b[20:60, 92:132] = L["face"]
        self.assertEqual(alignment_score(a, b, garment_occlusion=np.ones(a.shape, bool)), 0)
        # Trying to carve away every mismatch overlaps the visible shifted face.
        self.assertEqual(alignment_score(a, b, garment_occlusion=a != b), 0)
        with self.assertRaises(ValueError):
            alignment_score(a, b, garment_occlusion=np.zeros((20, 20), bool))
        with self.assertRaises(ValueError):
            alignment_score(a, b[:-1], garment_occlusion=np.zeros(a.shape, bool))
        with self.assertRaises(ValueError):
            alignment_score(a[None], b[None], garment_occlusion=np.zeros((1, *a.shape), bool))

    def test_personal_lower_body_check_retains_original_behavior(self):
        a, b = pose(), pose()
        b[120:145, 65:135] = L["upper_clothes"]
        original = alignment_score(a, b, include_face=False)
        self.assertEqual(original, 55 / 80)
        self.assertEqual(alignment_score(a, b, include_face=False, garment_occlusion=np.ones(a.shape, bool)), original)

    def test_old_cached_alignment_is_recomputed_without_rerender(self):
        a, b = pose(), pose()
        b[48:60, 80:120] = L["upper_clothes"]
        b[120:145, 65:135] = L["upper_clothes"]
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            styled = folder / "styled-1024.png"
            Image.new("RGB", (200, 200)).save(styled)
            np.savez_compressed(folder / "masks-pose.npz", alignment=np.float32(0))
            worker = Worker(Mock(), Mock(), Mock(), {}, folder / "state", selfie_validator=Mock())
            worker.pose = Mock(return_value={"labels": a})
            parser = Mock()
            parser.parse.return_value = b
            worker.human = Mock(return_value=parser)
            masks = worker.garment_masks("render", (styled, folder / "unused-2k.png"), "pose")
            self.assertTrue(masks["garment"].any())
            self.assertTrue((folder / f"masks-v{GARMENT_MASK_VERSION}-pose.npz").is_file())
            parser.parse.assert_called_once()
            worker.garment_masks("render", (styled, folder / "unused-2k.png"), "pose")
            parser.parse.assert_called_once()  # New cache reuses the corrected score.
            worker.comfy.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
