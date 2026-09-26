"""Controlled local texture tests for the existing identity workflow."""
import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from compare_jon_checkpoints import request, BASE

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--steps',type=int,default=80)
parser.add_argument('--resolution',type=int,default=0)
parser.add_argument('--strength',type=float,default=0.8)
parser.add_argument('--bfs-strength',type=float,default=1.0)
parser.add_argument('--generic',action='store_true')
parser.add_argument('--trigger',default='')
parser.add_argument('--name',default='neutral80')
args=parser.parse_args()
output = root / 'quality-comparisons'
output.mkdir(exist_ok=True)
api = json.loads((root / 'Qwen21_Jon_Neutral.api.json').read_text(encoding='utf-8-sig'))
workflow = json.loads((root / 'Qwen21_Jon_Neutral.json').read_text(encoding='utf-8-sig'))
api['9']['inputs']['steps'] = args.steps
api['8']['inputs']['resolution'] = args.resolution
api['14']['inputs']['strength_model'] = args.strength
api['4']['inputs']['strength_model'] = args.bfs_strength
api['11']['inputs']['filename_prefix'] = 'Qwen21_Quality/' + args.name
nodes = {str(n['id']): n for n in workflow['nodes']}
if args.generic:
    prompt=(root/'universal-prompt.txt').read_text(encoding='utf-8').strip()
    if args.trigger:
        prompt+='\nThe person in reference image 2 is '+args.trigger+'. Use that person\'s identity and distinctive facial features.'
    api['8']['inputs']['prompt']=prompt
    nodes['8']['widgets_values'][0]=prompt
    nodes['8']['widgets_values_named']['prompt']=prompt
nodes['9']['widgets_values'][2] = args.steps
nodes['9']['widgets_values_named']['steps'] = args.steps
nodes['9']['title'] = f'Quality test - {args.steps} steps'
nodes['8']['widgets_values'][2] = args.resolution
nodes['8']['widgets_values_named']['resolution'] = args.resolution
nodes['14']['widgets_values'][1] = args.strength
nodes['14']['widgets_values_named']['strength_model'] = args.strength
nodes['4']['widgets_values'][1] = args.bfs_strength
nodes['4']['widgets_values_named']['strength_model'] = args.bfs_strength
nodes['11']['widgets_values'][0] = api['11']['inputs']['filename_prefix']
nodes['11']['widgets_values_named']['filename_prefix'] = api['11']['inputs']['filename_prefix']
(output / (args.name+'.api.json')).write_text(json.dumps(api, indent=2), encoding='utf-8')
(output / (args.name+'.json')).write_text(json.dumps(workflow, indent=2), encoding='utf-8')
start = time.monotonic()
submitted = request('/prompt', {'prompt':api,'client_id':'codex-quality-test','extra_data':{'extra_pnginfo':{'workflow':workflow}}})
pid = submitted['prompt_id']
print(f'Queued {args.name}: {pid}', flush=True)
while True:
    history = request('/history/' + pid)
    if pid in history:
        break
    if time.monotonic() - start > 900:
        raise TimeoutError(pid)
    time.sleep(5)
result = history[pid]
(output / (args.name+'.history.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
if result['status']['status_str'] != 'success':
    raise RuntimeError(result['status'])
item=result['outputs']['11']['images'][0]
with urllib.request.urlopen(BASE+'/view?'+urllib.parse.urlencode(item),timeout=60) as response:
    (output/(args.name+'.png')).write_bytes(response.read())
print(f'Completed in {time.monotonic()-start:.2f}s',flush=True)
