"""Arrange unmodified render outputs side by side for visual review."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parent
items = [
    ('First pass: identity', root / 'realism-comparisons/subtle004_node11.png'),
    ('Second pass: subtle texture', root / 'realism-comparisons/subtle004.png'),
]
canvas = Image.new('RGB', (1600, 562), '#171a1e')
draw = ImageDraw.Draw(canvas)
font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 25)
for index, (label, path) in enumerate(items):
    image = Image.open(path).convert('RGB')
    assert image.size == (800, 512)
    canvas.paste(image, (index * 800, 50))
    draw.text((index * 800 + 22, 10), label, font=font, fill='white')
canvas.save(root / 'Realism_two_pass_comparison.png')
