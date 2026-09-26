"""Display equal-size crops of the original and two neural upscalers."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root=Path(__file__).resolve().parent/'tryon-results'
names=[('Original enlarged','TryOn_final.png'),('Real-ESRGAN 4x','TryOn_4K_RealESRGAN.png'),('Nomos 4x','TryOn_4K_Nomos.png')]
images=[]
for label,name in names:
    im=Image.open(root/name).convert('RGB')
    if im.size==(1024,1024):
        im=im.resize((4096,4096),Image.Resampling.LANCZOS)
    images.append((label,im))
font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',24)
for part,box in [('face',(414*4,85*4,558*4,229*4)),('shirt',(434*4,330*4,578*4,474*4))]:
    tile=576
    canvas=Image.new('RGB',(tile*3,tile+52),'#171a1e')
    draw=ImageDraw.Draw(canvas)
    for i,(label,im) in enumerate(images):
        canvas.paste(im.crop(box),(i*tile,52))
        draw.text((i*tile+18,12),label,font=font,fill='white')
    canvas.save(root/('Upscale_'+part+'_comparison.png'))
    selected=Image.new('RGB',(tile*2,tile+52),'#171a1e')
    selected_draw=ImageDraw.Draw(selected)
    for i,(label,im) in enumerate([('Before: original enlarged',images[0][1]),('After: Nomos 4K',images[2][1])]):
        selected.paste(im.crop(box),(i*tile,52))
        selected_draw.text((i*tile+18,12),label,font=font,fill='white')
    selected.save(root/('4K_'+part+'_before_after.png'))
print('Saved face and shirt crop comparisons at 1:1 output pixels.')
