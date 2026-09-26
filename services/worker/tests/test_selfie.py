import random
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from services.worker.selfie import validate_selfie


class SelfieTests(unittest.TestCase):
    def test_single_clear_face_required_without_identity_or_liveness_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.png"
            Image.frombytes("RGB", (512, 512), random.Random(42).randbytes(512 * 512 * 3)).save(path)
            with patch("services.worker.photo_selection._detect_faces", return_value=[(100, 100, 220, 220)]):
                self.assertEqual(validate_selfie(path)["face_count"], 1)
            for boxes in ([], [(100, 100, 220, 220), (1, 1, 110, 110)], [(100, 100, 50, 50)]):
                with patch("services.worker.photo_selection._detect_faces", return_value=boxes), self.assertRaisesRegex(ValueError, "Retake your live selfie"):
                    validate_selfie(path)

    def test_unreadable_selfie_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.jpg"
            path.write_bytes(b"not an image")
            with self.assertRaisesRegex(ValueError, "Retake your live selfie"):
                validate_selfie(path)
