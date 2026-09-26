"""Audit and collect the complete try-on job submitted through ComfyUI."""
import json
import time
from pathlib import Path
import requests

root=Path(__file__).resolve().parent
out=root/'tryon-results'
q=json.loads((out/'neutral_full_ui.queue.json').read_text(encoding='utf-8-sig'))
pid=q[1]
expected=json.loads((root/'Qwen21_Universal_TryOn.api.json').read_text())
for key,node in expected.items():
    actual=q[2][key]
    assert actual['class_type']==node['class_type'],key
    actual_inputs={k:v for k,v in actual['inputs'].items() if k!='compare_view'}
    expected_inputs={k:v for k,v in node['inputs'].items() if k!='compare_view'}
    assert actual_inputs==expected_inputs,(key,actual_inputs,expected_inputs)
print('UI generation inputs match the saved try-on API.',flush=True)
for _ in range(600):
    h=requests.get('http://127.0.0.1:8188/history/'+pid,timeout=30).json().get(pid)
    if h:
        (out/'neutral_full_ui.history.json').write_text(json.dumps(h,indent=2))
        assert h['status']['status_str']=='success',h['status']
        for n,name in [('24','TryOn_final.png'),('18','TryOn_baseline.png'),('11','TryOn_first_pass.png')]:
            data=h['outputs'][n]['images'][0]
            r=requests.get('http://127.0.0.1:8188/view',params=data,timeout=60)
            r.raise_for_status()
            (out/name).write_bytes(r.content)
            print(str(out/name),flush=True)
        print('Full UI try-on succeeded.',flush=True)
        break
    time.sleep(3)
else:
    raise TimeoutError(pid)
