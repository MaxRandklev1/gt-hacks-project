"""CPU human parsing (SegFormer-B2, ATR labels) for automatic try-on compositing masks.

Weights: mattmdjaga/segformer_b2_clothes ONNX export, NVIDIA SegFormer licence (non-commercial
research/evaluation). They are downloaded separately into comfy-identity/parsing-assets/.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "comfy-identity/parsing-assets/model.onnx"
LABELS = ("background", "hat", "hair", "sunglasses", "upper_clothes", "skirt", "pants", "dress", "belt",
          "left_shoe", "right_shoe", "face", "left_leg", "right_leg", "left_arm", "right_arm", "bag", "scarf")
# Label groups used by the compositor.
GROUPS = {
    "head": ("hat", "hair", "sunglasses", "face", "scarf"),   # Head coverings count as part of the person's look.
    "hair": ("hat", "hair", "scarf"),
    "face": ("face", "sunglasses"),
    "garment": ("upper_clothes", "dress"),
    "arms": ("left_arm", "right_arm"),
    "person": LABELS[1:],
}
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class HumanParser:
    def __init__(self, model_path=DEFAULT_MODEL, size=768):
        import onnxruntime
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"Human-parsing model is missing: {model_path}")
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.size = size

    def logits(self, image):
        resized = image.convert("RGB").resize((self.size, self.size), Image.BILINEAR)
        array = (np.asarray(resized, dtype=np.float32) / 255.0 - MEAN) / STD
        batch = array.transpose(2, 0, 1)[None]
        return self.session.run(None, {self.input: batch})[0][0]  # (labels, h/4, w/4)

    def parse(self, image, flip=True):
        """Label map at the image's own resolution. The flipped pass (left/right labels swapped) steadies edges."""
        logits = self.logits(image)
        if flip:
            flipped = self.logits(image.transpose(Image.FLIP_LEFT_RIGHT))[:, :, ::-1]
            order = list(range(len(LABELS)))
            for a, b in (("left_shoe", "right_shoe"), ("left_leg", "right_leg"), ("left_arm", "right_arm")):
                i, j = LABELS.index(a), LABELS.index(b)
                order[i], order[j] = j, i
            logits = (logits + flipped[order]) / 2
        import cv2
        width, height = image.size
        upsampled = np.stack([cv2.resize(channel, (width, height), interpolation=cv2.INTER_LINEAR) for channel in logits])
        return upsampled.argmax(0).astype(np.uint8)


def group_mask(labels, group):
    return np.isin(labels, [LABELS.index(name) for name in GROUPS[group]])
