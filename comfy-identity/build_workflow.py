import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INFO = json.loads((ROOT / 'object_info.json').read_text(encoding='utf-8-sig'))
PROMPT = '''head_swap: start with <image1> as the base image. Replace the person's head with the identity from <image2>, faithfully preserving the reference person's facial structure, eyes, nose, mouth, hair, beard, age and recognizable features. Reconstruct that identity in the original head rotation, tilt, gaze direction and facial expression of <image1>. Keep the original head placement, natural scale and neck connection. Use <image2> for identity and natural skin tone, while retaining the lighting and image style of <image1>.
Make every exposed skin region belong to the same person: harmonize the face, ears, neck, shoulders, chest, arms, hands and any visible legs to the reference person's natural skin tone, with consistent undertones and realistic shading under the original lighting. Keep skin texture and anatomical details natural. Do not copy the reference photograph's color cast, clothing, background, pose or expression.
Preserve the body shape, pose, clothing, garment edges, folds, accessories, camera angle, framing and background from <image1>. Only change the identity of the head and the color of exposed skin. Sharp facial detail, seamless hairline and jaw-to-neck transition.'''

nodes = []
links = []
api = {}

def node(ident, kind, title, pos, values=None, size=None, widgets=None, inputs=None):
    info = INFO[kind]
    values = values or {}
    schema = {**info.get('input', {}).get('required', {}), **info.get('input', {}).get('optional', {})}
    slots = []
    for name, spec in schema.items():
        dtype = spec[0]
        if dtype == 'COMFY_AUTOGROW_V3':
            continue
        is_widget = isinstance(dtype, list) or dtype in ('INT', 'FLOAT', 'BOOLEAN', 'STRING', 'COMBO')
        if name not in values and is_widget:
            continue
        slot = {'name': name, 'type': 'COMBO' if isinstance(dtype, list) else dtype, 'link': None}
        if is_widget:
            slot['widget'] = {'name': name}
        slots.append(slot)
    slots.extend(inputs or [])
    out_names = info.get('output_name', info.get('output', []))
    outputs = [{'name': out_names[i], 'type': dtype, 'links': []} for i, dtype in enumerate(info.get('output', []))]
    n = {'id': ident, 'type': kind, 'title': title, 'pos': pos, 'size': size or [330, 160], 'flags': {}, 'order': len(nodes), 'mode': 0, 'inputs': slots, 'outputs': outputs, 'properties': {'Node name for S&R': kind}, 'widgets_values': widgets if widgets is not None else list(values.values()), 'widgets_values_named': values}
    nodes.append(n)
    api[str(ident)] = {'class_type': kind, 'inputs': dict(values), '_meta': {'title': title}}
    return n

def connect(source, output, target, input_name):
    src = next(n for n in nodes if n['id'] == source)
    dst = next(n for n in nodes if n['id'] == target)
    slot = next(i for i, item in enumerate(dst['inputs']) if item['name'] == input_name)
    ident = len(links) + 1
    dtype = src['outputs'][output]['type']
    links.append([ident, source, output, target, slot, dtype])
    src['outputs'][output]['links'].append(ident)
    dst['inputs'][slot]['link'] = ident
    api[str(target)]['inputs'][input_name] = [str(source), output]

node(1, 'LoadImage', '1 - BODY / POSE / CLOTHES (base image)', [20, 60], {'image': 'Qwen_image_2.1_00008.png'}, [350, 390], widgets=['Qwen_image_2.1_00008.png', 'image'])
node(2, 'LoadImage', '2 - PERSON / FACE (identity reference)', [20, 510], {'image': 'Screenshot 2026-09-22 014339.png'}, [350, 440], widgets=['Screenshot 2026-09-22 014339.png', 'image'])
node(3, 'UnetLoaderGGUF', 'Qwen Image 2.1 - installed Q8 model', [440, 60], {'unet_name': 'qwen-image-2.1-Q8_0.gguf'}, [390, 90])
node(4, 'LoraLoaderModelOnly', 'BFS Head V1 - Qwen 2.1 identity LoRA', [440, 210], {'lora_name': 'bfs_head_v1_qwen_2.1.safetensors', 'strength_model': 1.0}, [390, 120])
node(5, 'QwenImage21Cache', 'Reference cache', [440, 390], {'device': 'auto', 'dtype': 'default'}, [390, 100])
node(6, 'CLIPLoader', 'Qwen 3 VL - existing text encoder', [440, 570], {'clip_name': 'qwen3vl_8b_int8_convrot.safetensors', 'type': 'qwen_image', 'device': 'default'}, [390, 130])
node(7, 'VAELoader', 'Qwen Image 2.1 VAE', [440, 770], {'vae_name': 'qwen_image_2.1_vae_bf16.safetensors'}, [390, 80])
node(8, 'TextEncodeQwenImage21', '3 - Identity + exposed-skin instruction', [910, 60], {'prompt': PROMPT, 'negative_prompt': '', 'resolution': 0}, [570, 710], inputs=[{'name': 'images.image_1', 'type': 'IMAGE', 'link': None}, {'name': 'images.image_2', 'type': 'IMAGE', 'link': None}])
node(9, 'KSampler', '4 - Quality render (40 steps)', [1560, 60], {'seed': 42, 'steps': 40, 'cfg': 1.0, 'sampler_name': 'euler', 'scheduler': 'simple', 'denoise': 1.0}, [340, 300], widgets=[42, 'fixed', 40, 1.0, 'euler', 'simple', 1.0])
nodes[-1]['widgets_values_named']['control_after_generate'] = 'fixed'
node(10, 'VAEDecode', 'Decode result', [1560, 440], {}, [340, 80])
node(11, 'SaveImage', '5 - Saved identity + skin result', [1990, 60], {'filename_prefix': 'Qwen21_Identity/identity_skin'}, [620, 600])
node(12, 'ImageCompare', 'Original / result comparison', [1990, 760], {}, [620, 560])
nodes[-1]['inputs'] = [
    {'name': 'image_a', 'type': 'IMAGE', 'shape': 7, 'link': None},
    {'name': 'image_b', 'type': 'IMAGE', 'shape': 7, 'link': None},
    {'name': 'compare_view', 'type': 'IMAGECOMPARE', 'widget': {'name': 'compare_view'}, 'link': None},
]
api['12']['inputs']['compare_view'] = ''
for args in [(3,0,4,'model'),(4,0,5,'model'),(6,0,8,'clip'),(7,0,8,'vae'),(1,0,8,'images.image_1'),(2,0,8,'images.image_2'),(5,0,9,'model'),(8,0,9,'positive'),(8,1,9,'negative'),(8,2,9,'latent_image'),(9,0,10,'samples'),(7,0,10,'vae'),(10,0,11,'images'),(1,0,12,'image_a'),(10,0,12,'image_b')]:
    connect(*args)

note = '''# Qwen 2.1 - Face identity + matching skin

1. Load the body/pose/clothing image on the left, then a clear face reference beneath it.
2. Run. The head-swap adapter is set to its author's starting strength of 1.0.
3. Inspect facial likeness, head angle, neck transition and all exposed skin in the comparison.

The identity LoRA transfers the whole head, including hair. Matching exposed skin is requested by the prompt; it is not a separate trained skin adapter.

40 steps, Euler, CFG 1; no acceleration LoRA required. Resolution 0 preserves each input's size rounded to multiples of 32. Start with moderate images on 16 GB VRAM. Seed is fixed for comparisons.

Generative editing can still alter clothing/background pixels. This workflow does not guarantee exact pixel preservation or perfect likeness. Your original saved workflows are unchanged.

LoRA: Alissonerdx/BFS-Best-Face-Swap / bfs_head_v1_qwen_2.1.safetensors
Guide: https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap/blob/main/docs/qwen-image-2.1.md'''
nodes.append({'id': 13, 'type': 'MarkdownNote', 'title': 'Start here', 'pos': [910, 860], 'size': [970, 450], 'flags': {}, 'order': 12, 'mode': 0, 'inputs': [], 'outputs': [], 'properties': {}, 'widgets_values': [note], 'widgets_values_named': {'text': note}})
workflow = {'id': str(uuid.uuid4()), 'revision': 0, 'last_node_id': 13, 'last_link_id': len(links), 'nodes': nodes, 'links': links, 'groups': [], 'config': {}, 'extra': {'ds': {'scale': 0.52, 'offset': [60, 80]}}, 'version': 0.4}
(ROOT / 'Qwen21_Identity_Skin.json').write_text(json.dumps(workflow, indent=2), encoding='utf-8')
(ROOT / 'Qwen21_Identity_Skin.api.json').write_text(json.dumps(api, indent=2), encoding='utf-8')
print('Created workflow and API graph:', len(nodes), 'nodes,', len(links), 'links')
