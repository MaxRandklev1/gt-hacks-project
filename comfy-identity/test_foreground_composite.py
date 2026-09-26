"""Test Comfy's native foreground composite on the approved saved image pair."""
import json
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'realism-comparisons'
BASE = 'http://127.0.0.1:8188'
def upload(name):
    with (OUT / name).open('rb') as f:
        r = requests.post(BASE + '/upload/image', files={'image': (name, f, 'image/png')}, data={'overwrite': 'true', 'subfolder': 'realism_mask_test'}, timeout=60)
    r.raise_for_status()
    d=r.json()
    return d['subfolder'] + '/' + d['name']

p = {
    '1': {'class_type': 'LoadImage', 'inputs': {'image': upload('subtle004_node18.png')}},
    '2': {'class_type': 'LoadImage', 'inputs': {'image': upload('subtle004.png')}},
    '3': {'class_type': 'LoadBackgroundRemovalModel', 'inputs': {'bg_removal_name': 'birefnet.safetensors'}},
    '4': {'class_type': 'RemoveBackground', 'inputs': {'bg_removal_model': ['3', 0], 'image': ['1', 0]}},
    '5': {'class_type': 'ImageCompositeMasked', 'inputs': {'destination': ['1', 0], 'source': ['2', 0], 'mask': ['4', 0], 'x': 0, 'y': 0, 'resize_source': False}},
    '6': {'class_type': 'SaveImage', 'inputs': {'images': ['5', 0], 'filename_prefix': 'Universal_Identity_TwoPass/masked_approved'}},
    '7': {'class_type': 'MaskToImage', 'inputs': {'mask': ['4', 0]}},
    '8': {'class_type': 'SaveImage', 'inputs': {'images': ['7', 0], 'filename_prefix': 'Universal_Identity_TwoPass/foreground_mask'}},
}
(OUT/'masked_approved.api.json').write_text(json.dumps(p,indent=2))
r=requests.post(BASE+'/prompt',json={'prompt':p},timeout=60)
r.raise_for_status()
print(r.json(),flush=True)
pid=r.json()['prompt_id']
for _ in range(150):
    h=requests.get(BASE+'/history/'+pid,timeout=30).json().get(pid)
    if h:
        (OUT/'masked_approved.history.json').write_text(json.dumps(h,indent=2))
        if h['status']['status_str'] != 'success':
            raise RuntimeError(h['status'])
        for n,name in [('6','masked_approved.png'),('8','foreground_mask.png')]:
            d=h['outputs'][n]['images'][0]
            b=requests.get(BASE+'/view',params=d,timeout=30)
            b.raise_for_status()
            (OUT/name).write_bytes(b.content)
        print('Saved masked_approved.png and foreground_mask.png',flush=True)
        break
    time.sleep(2)
else:
    raise TimeoutError(pid)
