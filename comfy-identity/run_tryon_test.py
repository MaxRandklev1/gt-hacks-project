"""Run and collect a reproducible local try-on graph, optionally first pass only."""
import argparse
import json
import time
from pathlib import Path
import requests

root=Path(__file__).resolve().parent
out=root/'tryon-results'
out.mkdir(exist_ok=True)
parser=argparse.ArgumentParser()
parser.add_argument('--api',default='Qwen21_Universal_TryOn.api.json')
parser.add_argument('--name',required=True)
parser.add_argument('--first-only',action='store_true')
args=parser.parse_args()
p=json.loads((root/args.api).read_text(encoding='utf-8-sig'))
w=json.loads((root/'Qwen21_Universal_TryOn.json').read_text(encoding='utf-8-sig'))
if args.first_only:
    p={k:v for k,v in p.items() if int(k) in {1,3,4,5,6,7,8,9,14,15,16,17,18,31,32}}
    p['18']['inputs']['filename_prefix']='Universal_TryOn/tests/'+args.name
(out/(args.name+'.api.json')).write_text(json.dumps(p,indent=2))
resp=requests.post('http://127.0.0.1:8188/prompt',json={'prompt':p,'extra_data':{'extra_pnginfo':{'workflow':w}}},timeout=60)
resp.raise_for_status()
job=resp.json()
(out/(args.name+'.job.json')).write_text(json.dumps(job,indent=2))
print(json.dumps(job),flush=True)
pid=job['prompt_id']
start=time.monotonic()
for _ in range(600):
    h=requests.get('http://127.0.0.1:8188/history/'+pid,timeout=30).json().get(pid)
    if h:
        (out/(args.name+'.history.json')).write_text(json.dumps(h,indent=2))
        if h['status']['status_str']!='success':
            raise RuntimeError(h['status'])
        for n,data in h['outputs'].items():
            if p[n]['class_type']=='SaveImage':
                for i,item in enumerate(data.get('images',[])):
                    r=requests.get('http://127.0.0.1:8188/view',params=item,timeout=60)
                    r.raise_for_status()
                    path=out/(args.name+'_node'+n+'_'+str(i)+'.png')
                    path.write_bytes(r.content)
                    print(str(path),flush=True)
        print(f'Completed in {time.monotonic()-start:.1f}s',flush=True)
        break
    time.sleep(3)
else:
    raise TimeoutError(pid)
