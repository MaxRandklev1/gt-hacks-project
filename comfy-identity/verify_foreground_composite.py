"""Check protected background and preserve the user-approved subject pixels."""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

root=Path(__file__).resolve().parent
folder=root/'realism-comparisons'
first=np.array(Image.open(folder/'subtle004_node18.png').convert('RGB')).astype(int)
refined=np.array(Image.open(folder/'subtle004.png').convert('RGB')).astype(int)
final=np.array(Image.open(folder/'masked_approved.png').convert('RGB')).astype(int)
mask=np.array(Image.open(folder/'foreground_mask.png').convert('L'))
bg=mask==0
fg=mask>=254
report={
    'background_pixels':int(bg.sum()),
    'background_mean_abs_difference':float(np.abs(final-first)[bg].mean()),
    'background_max_difference':int(np.abs(final-first)[bg].max()),
    'foreground_pixels':int(fg.sum()),
    'foreground_mean_abs_difference':float(np.abs(final-refined)[fg].mean()),
    'foreground_max_difference':int(np.abs(final-refined)[fg].max()),
}
assert report['background_max_difference']<=1
assert report['foreground_max_difference']<=1
(folder/'foreground_preservation_report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
items=[('Before: full-image refinement','subtle004.png'),('After: background protected','masked_approved.png')]
canvas=Image.new('RGB',(1600,562),'#171a1e')
draw=ImageDraw.Draw(canvas)
font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',25)
for i,(label,name) in enumerate(items):
    canvas.paste(Image.open(folder/name).convert('RGB'),(800*i,50))
    draw.text((800*i+22,10),label,font=font,fill='white')
canvas.save(root/'Realism_background_protected_comparison.png')
