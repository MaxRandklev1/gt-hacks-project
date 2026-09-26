"""Upload the user-selected pose and garment to the local ComfyUI server."""
import json
from pathlib import Path
import requests

root=Path(__file__).resolve().parent
folder=root.parent/'ClothesSwap'
sources={
    'tryon_pose_base.png':folder/'ChatGPT Image Sep 26, 2026, 01_31_17 PM.png',
    'tryon_shirt_reference.png':folder/'Screenshot 2026-09-26 125422.png',
}
report={}
for name,path in sources.items():
    with path.open('rb') as f:
        r=requests.post('http://127.0.0.1:8188/upload/image',files={'image':(name,f,'image/png')},data={'overwrite':'true'},timeout=60)
    r.raise_for_status()
    report[name]={'source':str(path),'local_comfy_upload':r.json()}
(root/'tryon-results').mkdir(exist_ok=True)
(root/'tryon-results/inputs.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
