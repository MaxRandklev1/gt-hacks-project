"""Arrange source photos and the unmodified final render for review."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import sys

root=Path(__file__).resolve().parent
sys.path.insert(0, str(root.parent))
from services.worker.body_templates import body_template_source

items=[
    ('Fixed pose',body_template_source(root.parent/'ClothesSwap', 3)),
    ('Uploaded shirt',root.parent/'ClothesSwap/Screenshot 2026-09-26 125422.png'),
    ('Selected person + shirt',root/'tryon-results/TryOn_final.png'),
]
canvas=Image.new('RGB',(1536,566),'#171a1e')
draw=ImageDraw.Draw(canvas)
font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',23)
for i,(label,path) in enumerate(items):
    im=ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    im.thumbnail((512,512),Image.Resampling.LANCZOS)
    canvas.paste(im,(i*512+(512-im.width)//2,54+(512-im.height)//2))
    draw.text((i*512+20,12),label,font=font,fill='white')
canvas.save(root/'tryon-results/TryOn_comparison.png')
print(root/'tryon-results/TryOn_comparison.png')
