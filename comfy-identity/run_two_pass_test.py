"""Run a controlled second-pass test through the existing local ComfyUI API."""
import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from compare_jon_checkpoints import BASE, request

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    parser.add_argument('--denoise', type=float, default=0.2)
    parser.add_argument('--strength', type=float, default=0.65)
    args = parser.parse_args()
    api = json.loads((ROOT / 'Qwen21_Universal_Identity_TwoPass.api.json').read_text(encoding='utf-8-sig'))
    workflow = json.loads((ROOT / 'Qwen21_Universal_Identity_TwoPass.json').read_text(encoding='utf-8-sig'))
    sampler = next(k for k, v in api.items() if v['class_type'] == 'KSampler' and k != '9')
    lora = next(k for k, v in api.items() if v['class_type'] == 'LoraLoaderModelOnly' and k != '4')
    decoder = next(k for k, v in api.items() if v['class_type'] == 'VAEDecode' and v['inputs']['samples'] == [sampler, 0])
    final_save = next(k for k, v in api.items() if v['class_type'] == 'SaveImage' and v['inputs']['images'] == [decoder, 0])
    api[sampler]['inputs']['denoise'] = args.denoise
    api[lora]['inputs']['strength_model'] = args.strength
    api[final_save]['inputs']['filename_prefix'] = 'Universal_Identity_Realism/' + args.name
    nodes = {str(n['id']): n for n in workflow['nodes']}
    nodes[sampler]['widgets_values'][6] = args.denoise
    nodes[lora]['widgets_values'][1] = args.strength
    nodes[final_save]['widgets_values'][0] = api[final_save]['inputs']['filename_prefix']
    for ident, field, value in [(sampler, 'denoise', args.denoise), (lora, 'strength_model', args.strength), (final_save, 'filename_prefix', api[final_save]['inputs']['filename_prefix'])]:
        if 'widgets_values_named' in nodes[ident]:
            nodes[ident]['widgets_values_named'][field] = value
    output = ROOT / 'realism-comparisons'
    output.mkdir(exist_ok=True)
    for suffix, value in [('api.json', api), ('json', workflow)]:
        (output / f'{args.name}.{suffix}').write_text(json.dumps(value, indent=2), encoding='utf-8')
    submitted = request('/prompt', {'prompt': api, 'client_id': 'codex-two-pass-test', 'extra_data': {'extra_pnginfo': {'workflow': workflow}}})
    pid = submitted['prompt_id']
    print(f'Queued {args.name}: {pid}', flush=True)
    started = time.monotonic()
    while True:
        history = request('/history/' + pid)
        if pid in history:
            break
        if time.monotonic() - started > 900:
            raise TimeoutError(pid)
        time.sleep(3)
    result = history[pid]
    (output / f'{args.name}.history.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result['status']['status_str'] != 'success':
        raise RuntimeError(result['status'])
    for ident, data in result['outputs'].items():
        if 'images' not in data or api.get(ident, {}).get('class_type') != 'SaveImage':
            continue
        item = data['images'][0]
        destination = output / (f'{args.name}.png' if ident == final_save else f'{args.name}_node{ident}.png')
        with urllib.request.urlopen(BASE + '/view?' + urllib.parse.urlencode(item), timeout=60) as response:
            destination.write_bytes(response.read())
    print(f'Completed in {time.monotonic()-started:.2f}s: {output / (args.name + ".png")}', flush=True)


if __name__ == '__main__':
    main()
