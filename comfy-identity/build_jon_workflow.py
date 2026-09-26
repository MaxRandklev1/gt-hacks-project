"""Add a trained Qwen 2.1 identity adapter to the verified local BFS workflow."""
import argparse
import copy
import json
import uuid
from pathlib import Path

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('adapter_name', help='Installed ComfyUI LoRA filename')
parser.add_argument('--strength', type=float, default=0.8)
parser.add_argument('--bfs-adapter', default='bfs_head_v1_qwen_2.1.safetensors')
args = parser.parse_args()

workflow = json.loads((root / 'Qwen21_Identity_Skin.json').read_text(encoding='utf-8-sig'))
api = json.loads((root / 'Qwen21_Identity_Skin.api.json').read_text(encoding='utf-8-sig'))
by_id = {n['id']: n for n in workflow['nodes']}
new_id = workflow['last_node_id'] + 1
new_link_id = workflow['last_link_id'] + 1
bfs = by_id[4]
bfs['widgets_values'][0] = args.bfs_adapter
bfs['widgets_values_named']['lora_name'] = args.bfs_adapter
api['4']['inputs']['lora_name'] = args.bfs_adapter
cache = by_id[5]
old_link = next(link for link in workflow['links'] if link[1] == 4 and link[3] == 5)
identity = copy.deepcopy(bfs)
identity.update(id=new_id, title='Jon identity LoRA - likeness strength', pos=[440, 380])
identity['widgets_values'] = [args.adapter_name, args.strength]
identity['widgets_values_named'] = {'lora_name': args.adapter_name, 'strength_model': args.strength}
model_slot = next(i for i, s in enumerate(identity['inputs']) if s['name'] == 'model')
identity['inputs'][model_slot]['link'] = old_link[0]
identity['outputs'][0]['links'] = [new_link_id]
old_link[3:5] = [new_id, model_slot]
cache_slot = next(i for i, s in enumerate(cache['inputs']) if s['name'] == 'model')
cache['inputs'][cache_slot]['link'] = new_link_id
workflow['links'].append([new_link_id, new_id, 0, 5, cache_slot, 'MODEL'])
workflow['nodes'].append(identity)
cache['pos'] = [440, 560]
by_id[6]['pos'] = [440, 760]
by_id[7]['pos'] = [440, 970]

prompt = api['8']['inputs']['prompt'].replace(
    "Replace the person's head with the identity from <image2>",
    "Replace the person's head with j0n_person, the person shown in <image2>",
)
by_id[8]['widgets_values_named']['prompt'] = prompt
by_id[8]['widgets_values'][0] = prompt
api['8']['inputs']['prompt'] = prompt
api[str(new_id)] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['4', 0], 'lora_name': args.adapter_name, 'strength_model': args.strength}, '_meta': {'title': identity['title']}}
api['5']['inputs']['model'] = [str(new_id), 0]
by_id[11]['widgets_values'][0] = 'Qwen21_Jon/identity_dual_lora'
by_id[11]['widgets_values_named']['filename_prefix'] = 'Qwen21_Jon/identity_dual_lora'
api['11']['inputs']['filename_prefix'] = 'Qwen21_Jon/identity_dual_lora'
note = f'''# Jon identity + BFS head swap

1. Load the body/pose/clothing image and a clear reference photo of Jon.
2. Run. BFS provides the swap behavior; the second LoRA adds the trained identity.
3. Adjust the Jon strength independently. Higher values are not automatically better.

Keep the identity trigger `j0n_person` in the prompt. Both adapters target Qwen Image 2.1.
The installed identity adapter is `{args.adapter_name}`.
The pilot was trained from five cropped, captioned real photos; originals are preserved.

Inspect likeness, head angle, exposed skin and clothing. Training activity and clean blending do not establish accurate identity; compare the actual face. This remains a generative edit.'''
by_id[13]['widgets_values'] = [note]
by_id[13]['widgets_values_named'] = {'text': note}
workflow.update(id=str(uuid.uuid4()), last_node_id=new_id, last_link_id=new_link_id)
(root / 'Qwen21_Jon_Identity.json').write_text(json.dumps(workflow, indent=2), encoding='utf-8')
(root / 'Qwen21_Jon_Identity.api.json').write_text(json.dumps(api, indent=2), encoding='utf-8')
print('Prepared Jon dual-LoRA workflow. Import only after the named adapter is installed.')
