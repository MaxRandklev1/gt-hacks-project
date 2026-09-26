"""Render fixed-input local ComfyUI comparisons after identity training finishes."""
import argparse
import copy
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8188"
CASES = [
    ("step400_strength000", "Jon_Qwen21_rank16_step400.safetensors", 0.0),
    ("step200_strength080", "Jon_Qwen21_rank16_step200.safetensors", 0.8),
    ("step400_strength080", "Jon_Qwen21_rank16_step400.safetensors", 0.8),
    ("step400_strength110", "Jon_Qwen21_rank16_step400.safetensors", 1.1),
]


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ComfyUI {exc.code}: {exc.read().decode()}") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="jon-comparisons")
    parser.add_argument("--bfs-adapter")
    parser.add_argument("--cases", nargs="+", choices=[case[0] for case in CASES], default=[case[0] for case in CASES if case[2] > 0])
    args = parser.parse_args()
    out = ROOT / args.out_dir
    cases = [case for case in CASES if case[0] in args.cases]
    queue = request("/queue")
    if queue["queue_running"] or queue["queue_pending"]:
        raise RuntimeError("ComfyUI has queued work; wait before starting comparisons.")
    available = request("/object_info/LoraLoaderModelOnly")["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
    for _, filename, _ in cases:
        if filename not in available:
            raise RuntimeError(f"Install the trained adapter first: {filename}")
    if args.bfs_adapter and args.bfs_adapter not in available:
        raise RuntimeError(f"Install the BFS adapter first: {args.bfs_adapter}")
    template = json.loads((ROOT / "Qwen21_Jon_Identity.json").read_text(encoding="utf-8"))
    template_api = json.loads((ROOT / "Qwen21_Jon_Identity.api.json").read_text(encoding="utf-8"))
    identity_id = next(k for k, v in template_api.items() if v.get("_meta", {}).get("title", "").startswith("Jon identity"))
    out.mkdir(exist_ok=True)
    manifest = []
    for label, filename, strength in cases:
        api, workflow = copy.deepcopy(template_api), copy.deepcopy(template)
        nodes = {str(node["id"]): node for node in workflow["nodes"]}
        if args.bfs_adapter:
            api["4"]["inputs"]["lora_name"] = args.bfs_adapter
            nodes["4"]["widgets_values"][0] = args.bfs_adapter
            nodes["4"]["widgets_values_named"]["lora_name"] = args.bfs_adapter
        api[identity_id]["inputs"].update(lora_name=filename, strength_model=strength)
        nodes[identity_id]["widgets_values"] = [filename, strength]
        nodes[identity_id]["widgets_values_named"].update(lora_name=filename, strength_model=strength)
        prefix = "Qwen21_Jon/" + args.out_dir + "/" + label
        api["11"]["inputs"]["filename_prefix"] = prefix
        nodes["11"]["widgets_values"][0] = prefix
        nodes["11"]["widgets_values_named"]["filename_prefix"] = prefix
        (out / f"{label}.json").write_text(json.dumps(workflow, indent=2), encoding="utf-8")
        (out / f"{label}.api.json").write_text(json.dumps(api, indent=2), encoding="utf-8")
        start = time.monotonic()
        submitted = request("/prompt", {"prompt": api, "client_id": "codex-jon-comparison", "extra_data": {"extra_pnginfo": {"workflow": workflow}}})
        prompt_id = submitted["prompt_id"]
        (out / f"{label}.submission.json").write_text(json.dumps(submitted, indent=2), encoding="utf-8")
        print(f"Queued {label}: {prompt_id}", flush=True)
        while True:
            history = request("/history/" + prompt_id)
            if prompt_id in history:
                break
            if time.monotonic() - start > 900:
                raise TimeoutError(f"{label} still running; inspect ComfyUI queue before retrying.")
            time.sleep(5)
        result = history[prompt_id]
        (out / f"{label}.history.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        if result["status"]["status_str"] != "success":
            raise RuntimeError(f"{label} failed: {result['status']}")
        saved = []
        for index, item in enumerate(result["outputs"]["11"]["images"]):
            path = out / f"{label}_{index}.png"
            url = BASE + "/view?" + urllib.parse.urlencode(item)
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
            saved.append(str(path))
        manifest.append({"case": label, "adapter": filename, "strength": strength, "bfs_adapter": api['4']['inputs']['lora_name'], "seed": 42, "steps": 40, "prompt_id": prompt_id, "elapsed_seconds": round(time.monotonic() - start, 2), "outputs": saved})
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Completed {label} in {manifest[-1]['elapsed_seconds']}s", flush=True)


if __name__ == "__main__":
    main()
