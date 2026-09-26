"""Measure personal-LoRA effects through the existing local sampler, not image quality.

Run only after coordinating/pause of an idle cloud worker. Does not edit the app,
profiles, adapters or installed nodes. Outputs contain private graph/latent data
and must remain in the ignored training-comparisons directory.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time

import numpy as np
from safetensors.numpy import load

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from services.worker.comfy import ComfyClient, safe_id


def ancestors(graph, output):
    required = set()

    def visit(node_id):
        if node_id in required:
            return
        required.add(node_id)
        for value in graph[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph:
                visit(str(value[0]))

    visit(output)
    return {key: copy.deepcopy(value) for key, value in graph.items() if key in required}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()
    name, profile_id = safe_id(args.name), safe_id(args.profile)
    if len(name) > 110:
        parser.error("Experiment name must be at most 110 characters.")
    output = ROOT / "training-comparisons" / name
    client = ComfyClient()
    record_path = output / "manifest.json"
    if args.collect_only:
        record = json.loads(record_path.read_text())
        if record["profile"] != profile_id:
            raise ValueError("Profile differs from the saved experiment.")
        if len(record["cases"]) != 4:
            raise RuntimeError("Only some cases were submitted; inspect the local queue before continuing.")
    else:
        if output.exists():
            raise ValueError("Experiment exists; use --collect-only instead of resubmitting.")
        if not client.idle():
            raise RuntimeError("Local work is active. Do not disturb it.")
        profile = client.profile(profile_id)
        if not profile.get("training", {}).get("adapter_available"):
            raise RuntimeError("The selected trained adapter is unavailable.")
        graph = json.loads((ROOT / "Qwen21_Universal_TryOn_4K.api.json").read_text(encoding="utf-8-sig"))
        graph = ancestors(graph, "9")
        sampler = graph["9"]["inputs"]
        seed = sampler.pop("seed")
        sampler.pop("denoise")
        sampler.update(add_noise="enable", noise_seed=seed, start_at_step=0,
                       end_at_step=1, return_with_leftover_noise="enable")
        graph["9"]["class_type"] = "KSamplerAdvanced"
        graph["14"]["inputs"].update(profile_id=profile_id, use_trained_identity=True)
        graph["15"]["inputs"]["identity_instruction"] = (
            f"The person in reference image 2 is {profile['trigger']}. "
            "Use that person's identity and distinctive facial features."
        )
        graph["90"] = {"class_type": "SaveLatent", "inputs": {"samples": ["9", 0], "filename_prefix": ""}}
        output.mkdir(parents=True)
        record = {"profile": profile_id, "cases": [], "probe": "first Euler step of original 80-step schedule",
                  "limits": "Numerical causal probe only; not a finished image or identity-quality evaluation."}
        write_json(record_path, record)
        for index, strength in enumerate((0.0, 0.8, 4.0, 0.0)):
            if not client.idle():
                raise RuntimeError("Unexpected concurrent work; stop submitting diagnostics.")
            # Installed main.py processes /free by unloading models and resetting
            # the executor cache. History below must confirm the sampler wasn't cached.
            client.request("POST", "/free", json={"free_memory": True, "unload_models": True})
            time.sleep(3)
            case_graph = copy.deepcopy(graph)
            case_graph["14"]["inputs"]["identity_strength"] = strength
            case_graph["90"]["inputs"]["filename_prefix"] = f"LoRADiagnostic/{name}/case{index}"
            write_json(output / f"case{index}.api.json", case_graph)
            prompt_id = client.submit(case_graph, f"lora-probe-{name}-{index}")
            case = {"strength": strength, "promptId": prompt_id, "status": "submitted"}
            record["cases"].append(case)
            write_json(record_path, record)
            print(json.dumps({"submitted": index, **case}), flush=True)
            collect(client, case, index, output)
            write_json(record_path, record)
    arrays = []
    for index, case in enumerate(record["cases"]):
        if case.get("status") != "complete":
            collect(client, case, index, output)
            write_json(record_path, record)
        arrays.append(load((output / f"case{index}.latent").read_bytes())["latent_tensor"].astype(np.float64))
    metrics = []
    for index, array in enumerate(arrays):
        if not np.isfinite(array).all() or array.shape != arrays[0].shape:
            raise ValueError("Invalid or mismatched latent.")
        diff = array - arrays[0]
        metrics.append({"case": index, "strength": record["cases"][index]["strength"],
                        "relative_l2_vs_first_zero": float(np.linalg.norm(diff) / np.linalg.norm(arrays[0])),
                        "maximum_absolute_difference": float(np.abs(diff).max()),
                        "mean_absolute_difference": float(np.abs(diff).mean()),
                        "changed_values_percent": float((diff != 0).mean() * 100)})
    report = {"shape": list(arrays[0].shape), "metrics": metrics,
              "interpretation": "Compare LoRA deltas with the repeated-zero control. A positive delta establishes numerical effect, not improved identity."}
    write_json(output / "metrics.json", report)
    print(json.dumps(report, indent=2), flush=True)


def collect(client, case, index, output):
    history = client.wait_generation(case["promptId"], lambda: None, lambda: None)
    write_json(output / f"case{index}.history.json", history)
    cached = [str(node) for event, data in history.get("status", {}).get("messages", [])
              if event == "execution_cached" for node in data.get("nodes", [])]
    if "9" in cached:
        raise RuntimeError("Sampler was cached; this case is not an independent runtime measurement.")
    items = history.get("outputs", {}).get("90", {}).get("latents", [])
    if len(items) != 1 or items[0].get("type") != "output":
        raise RuntimeError("Expected one saved diagnostic latent.")
    item = items[0]
    data = client.request("GET", "/view", params={key: item[key] for key in ("filename", "subfolder", "type")}).content
    (output / f"case{index}.latent").write_bytes(data)
    case.update(status="complete", sampler_cached=False)
    print(json.dumps({"complete": index, "strength": case["strength"]}), flush=True)


if __name__ == "__main__":
    main()
