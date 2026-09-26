"""Export the selected local demo results without embedded workflow/EXIF metadata."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "comfy-identity/Jon_identity_neutral.png": "identity-neutral.png",
    "comfy-identity/Realism_background_protected_comparison.png": "skin-refinement-comparison.png",
    "comfy-identity/tryon-results/TryOn_comparison.png": "try-on-comparison.png",
    "comfy-identity/tryon-results/TryOn_final.png": "try-on-final-1024.png",
    "comfy-identity/tryon-results/TryOn_4K_Nomos.png": "try-on-final-4k.png",
    "comfy-identity/tryon-results/4K_face_before_after.png": "4k-face-comparison.png",
}


def main():
    missing = [source for source in ASSETS if not (ROOT / source).is_file()]
    if missing:
        raise SystemExit("Local demo outputs are required: " + ", ".join(missing))
    destination = ROOT / "docs" / "demo"
    destination.mkdir(parents=True, exist_ok=True)
    for source, filename in ASSETS.items():
        with Image.open(ROOT / source) as original:
            # A fresh image preserves pixels without carrying info/EXIF dictionaries.
            pixels = original.convert("RGB")
            clean = Image.frombytes("RGB", pixels.size, pixels.tobytes())
            target = destination / filename
            clean.save(target, format="PNG")
        with Image.open(target) as exported:
            assert not exported.info, f"Unexpected metadata in {filename}"
            assert exported.size == pixels.size
        print(f"{target.relative_to(ROOT)}: {pixels.width}x{pixels.height}")


if __name__ == "__main__":
    main()
