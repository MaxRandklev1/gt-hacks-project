"""Local selfie-quality screening; this is not identity, recency or liveness proof."""
from pathlib import Path

try:
    from .photo_selection import _analyze
except ImportError:
    from photo_selection import _analyze


def validate_selfie(path):
    record, usable = _analyze(Path(path), 0)
    if usable is None:
        raise ValueError("Take or choose a clear recent selfie. " + record["reason"])
    return record["metrics"]
