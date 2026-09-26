"""Arrange source photos and the unmodified final render for review."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

root=Path(__file__).resolve().parent
items=[
    ('Fixed pose',root.parent/'ClothesSwap/ChatGPT Image Sep 26, 2026, 01_31_17 PM.png'),
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
