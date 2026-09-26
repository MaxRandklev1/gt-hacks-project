"""Collect and audit the UI-triggered complete masked workflow run."""
import json
import time
from pathlib import Path
import requests

root=Path(__file__).resolve().parent
out=root/'realism-comparisons'
q=json.loads((out/'masked_full_ui.queue.json').read_text(encoding='utf-8-sig'))
pid=q[1]
expected=json.loads((root/'Qwen21_Universal_Identity_TwoPass.api.json').read_text())
for k,v in expected.items():
    actual=q[2][k]
    assert actual['class_type']==v['class_type'], k
    # Comparison widget state is display-only and serializes as a two-image list in the UI.
    actual_inputs={key:value for key,value in actual['inputs'].items() if key!='compare_view'}
    expected_inputs={key:value for key,value in v['inputs'].items() if key!='compare_view'}
    assert actual_inputs==expected_inputs, (k,actual_inputs,expected_inputs)
print('UI serialized all API nodes and inputs correctly.',flush=True)
for _ in range(240):
    h=requests.get('http://127.0.0.1:8188/history/'+pid,timeout=30).json().get(pid)
    if h:
        (out/'masked_full_ui.history.json').write_text(json.dumps(h,indent=2))
        assert h['status']['status_str']=='success',h['status']
        for n,name in [('24','masked_full_ui.png'),('18','masked_full_ui_baseline.png'),('11','masked_full_ui_first.png')]:
            d=h['outputs'][n]['images'][0]
            r=requests.get('http://127.0.0.1:8188/view',params=d,timeout=30)
            r.raise_for_status()
            (out/name).write_bytes(r.content)
        print('Complete masked workflow succeeded. Saved final, baseline, first-pass, and history.',flush=True)
        break
    time.sleep(2)
else:
    raise TimeoutError(pid)
