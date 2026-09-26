"""Local capture-quality screening; this is not identity or liveness proof."""
from pathlib import Path

try:
    from .photo_selection import _analyze
except ImportError:
    from photo_selection import _analyze


def validate_selfie(path):
    record, usable = _analyze(Path(path), 0)
    if usable is None:
        raise ValueError("Retake your live selfie. " + record["reason"])
    return record["metrics"]
