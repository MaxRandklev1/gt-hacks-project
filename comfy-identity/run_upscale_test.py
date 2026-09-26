"""Use native local ComfyUI upscaling on the exact saved final try-on output."""
import argparse
import json
import time
from pathlib import Path
import requests
from PIL import Image

root=Path(__file__).resolve().parent
out=root/'tryon-results'
parser=argparse.ArgumentParser()
parser.add_argument('--model',required=True)
parser.add_argument('--name',required=True)
args=parser.parse_args()
with (out/'TryOn_final.png').open('rb') as f:
    r=requests.post('http://127.0.0.1:8188/upload/image',files={'image':('tryon_final_1024.png',f,'image/png')},data={'overwrite':'true'},timeout=60)
r.raise_for_status()
p={
    '1':{'class_type':'LoadImage','inputs':{'image':'tryon_final_1024.png'}},
    '2':{'class_type':'UpscaleModelLoader','inputs':{'model_name':args.model}},
    '3':{'class_type':'ImageUpscaleWithModel','inputs':{'upscale_model':['2',0],'image':['1',0]}},
    '4':{'class_type':'SaveImage','inputs':{'images':['3',0],'filename_prefix':'Universal_TryOn/'+args.name}},
}
(out/(args.name+'.api.json')).write_text(json.dumps(p,indent=2))
r=requests.post('http://127.0.0.1:8188/prompt',json={'prompt':p},timeout=60)
r.raise_for_status()
job=r.json()
(out/(args.name+'.job.json')).write_text(json.dumps(job,indent=2))
print(job,flush=True)
t=time.monotonic()
for _ in range(600):
    h=requests.get('http://127.0.0.1:8188/history/'+job['prompt_id'],timeout=30).json().get(job['prompt_id'])
    if h:
        (out/(args.name+'.history.json')).write_text(json.dumps(h,indent=2))
        assert h['status']['status_str']=='success',h['status']
        info=h['outputs']['4']['images'][0]
        r=requests.get('http://127.0.0.1:8188/view',params=info,timeout=60)
        r.raise_for_status()
        path=out/(args.name+'.png')
        path.write_bytes(r.content)
        im=Image.open(path)
        assert im.size==(4096,4096),im.size
        print(json.dumps({'path':str(path),'size':im.size,'seconds':round(time.monotonic()-t,2)}),flush=True)
        break
    time.sleep(2)
else:
    raise TimeoutError(job['prompt_id'])
