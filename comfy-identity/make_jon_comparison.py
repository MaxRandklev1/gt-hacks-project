"""Make a labeled, unretouched comparison sheet from local inference outputs."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--result", default="jon-comparisons/step400_strength080_0.png")
parser.add_argument("--label", default="Jon LoRA + BFS")
parser.add_argument("--baseline", default="identity_skin_00001_.png")
parser.add_argument("--baseline-label", default="Previous BFS result")
args = parser.parse_args()
font_path = "C:/Windows/Fonts/segoeui.ttf"
heading = ImageFont.truetype(font_path, 26)
small = ImageFont.truetype(font_path, 20)
panels = [
    ("Reference photo", root / "Screenshot 2026-09-22 014339.png", (115, 50, 585, 690)),
    (args.baseline_label, root / args.baseline, (265, 12, 535, 285)),
    (args.label, root / args.result, (265, 12, 535, 285)),
]
sheet = Image.new("RGB", (1440, 760), "#171b22")
draw = ImageDraw.Draw(sheet)
for index, (label, path, crop) in enumerate(panels):
    left = index * 480
    draw.text((left + 18, 15), label, fill="white", font=heading)
    picture = Image.open(path).convert("RGB")
    face = ImageOps.contain(picture.crop(crop), (448, 420), Image.Resampling.LANCZOS)
    sheet.paste(face, (left + (480 - face.width) // 2, 65 + (420 - face.height) // 2))
    full = ImageOps.contain(picture, (448, 245), Image.Resampling.LANCZOS)
    sheet.paste(full, (left + (480 - full.width) // 2, 502 + (245 - full.height) // 2))
sheet.save(root / "Jon_identity_comparison.jpg", quality=95)
print(root / "Jon_identity_comparison.jpg")
