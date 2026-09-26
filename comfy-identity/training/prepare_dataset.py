import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parents[1] / 'Jon'
DEST = ROOT / 'dataset'
DEST.mkdir(exist_ok=True)

samples = [
    ('Screenshot 2026-09-22 014339.png', (115, 50, 585, 690), 'front-facing close-up from slightly below, smiling with teeth visible, full dark beard with gray strands, gray hoodie, warm indoor lighting'),
    ('Screenshot 2026-09-26 112758.png', (0, 0, 560, 620), 'tilted close-up, wide eyes and playful open-mouth expression with tongue out, full beard, gray hoodie, indoor lighting'),
    ('Screenshot 2026-09-26 112817.png', (55, 165, 435, 595), 'front-facing head-and-shoulders portrait, slight closed-mouth smile, short facial stubble, dark gray T-shirt, seated indoors under warm lighting'),
    ('Screenshot 2026-09-26 112821.png', (0, 10, 330, 475), 'three-quarter side view facing left with eyes toward the camera, raised eyebrows, short salt-and-pepper beard, swept-back gray hair, red T-shirt, indoor lighting'),
    ('Screenshot 2026-09-26 112831.png', (35, 15, 375, 445), 'three-quarter head-and-shoulders portrait, closed-mouth slight smile, full beard, side-swept dark hair, gray hoodie, direct flash against a dark background'),
]
metadata = []
manifest = []
sheet = Image.new('RGB', (1500, 440), '#20242b')
draw = ImageDraw.Draw(sheet)
for index, (name, box, description) in enumerate(samples, 1):
    original = Image.open(SOURCE / name)
    crop = ImageOps.exif_transpose(original).convert('RGB').crop(box)
    filename = f'jon_{index:02d}.png'
    crop.save(DEST / filename)
    caption = 'photo of j0n_person, ' + description
    metadata.append({'file_name': filename, 'text': caption})
    manifest.append({'source': name, 'source_sha256': hashlib.sha256((SOURCE / name).read_bytes()).hexdigest(), 'source_size': original.size, 'crop': box, 'output': filename, 'output_size': crop.size, 'caption': caption})
    thumb = ImageOps.contain(crop, (286, 386))
    sheet.paste(thumb, ((index - 1) * 300 + 7, 32))
    draw.text(((index - 1) * 300 + 7, 9), filename, fill='white')

(DEST / 'metadata.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in metadata), encoding='utf-8')
(ROOT / 'dataset-manifest.json').write_text(json.dumps({'trigger': 'j0n_person', 'samples': manifest, 'excluded': {'Screenshot 2026-09-26 112812.png': '118 x 237 pixels; very little face detail', 'Screenshot 2026-09-26 112836.png': '166 x 451 pixels; very little face detail', 'Screenshot 2026-09-26 112826.png': 'Small face, hat and glasses; held out from this first training set'}}, indent=2), encoding='utf-8')
sheet.save(ROOT / 'dataset-contact-sheet.jpg')
print(json.dumps({'selected': len(metadata), 'dataset': str(DEST), 'trigger': 'j0n_person'}))
