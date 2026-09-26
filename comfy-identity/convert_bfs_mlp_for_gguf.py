"""Split fused BFS gate/up LoRAs without changing their learned update.

All tensors stay on CPU. The source adapter is never overwritten. Qwen 2.1's
fused MLP orders rows as [gate; up], so A is copied and B is split by output row.
"""
import argparse
import hashlib
import json
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import load_file, save_file


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if source == destination or destination.exists():
        raise ValueError("Use a new destination; existing files are never overwritten.")

    original = load_file(str(source), device="cpu")
    with safe_open(str(source), framework="pt", device="cpu") as handle:
        metadata = dict(handle.metadata() or {})
    converted = dict(original)
    split_modules = []
    for key in sorted(original):
        suffix = ".img_mlp.gate_up.lora_A.weight"
        if not key.endswith(suffix):
            continue
        prefix = key.removesuffix(".lora_A.weight")
        a = original[prefix + ".lora_A.weight"]
        b = original[prefix + ".lora_B.weight"]
        if a.ndim != 2 or b.ndim != 2 or b.shape[1] != a.shape[0] or b.shape[0] % 2:
            raise ValueError(f"Unexpected fused adapter shapes: {prefix}")
        # Verified BFS/Qwen 2.1 dimensions. Fail instead of guessing another architecture.
        if a.shape != (64, 4096) or b.shape != (24576, 64):
            raise ValueError(f"Not the reviewed BFS rank-64 layout: {prefix}: {a.shape}, {b.shape}")
        # This public Diffusers prefix is accepted by ComfyUI for both fused and
        # separate MLP base weights. Unaffected original keys retain their prefix.
        public_prefix = prefix.removeprefix("diffusion_model.")
        public_prefix = "transformer." + public_prefix
        half = b.shape[0] // 2
        for branch, rows in (("gate_layer", slice(0, half)), ("proj", slice(half, None))):
            branch_prefix = public_prefix.replace(".gate_up", "." + branch)
            converted[branch_prefix + ".lora_A.weight"] = a.clone().contiguous()
            converted[branch_prefix + ".lora_B.weight"] = b[rows].clone().contiguous()
        del converted[prefix + ".lora_A.weight"]
        del converted[prefix + ".lora_B.weight"]
        split_modules.append((prefix, public_prefix))

    if len(split_modules) != 32:
        raise ValueError(f"Expected 32 fused modules, found {len(split_modules)}.")
    if any(".gate_up." in key for key in converted):
        raise ValueError("Unhandled fused adapter keys remain; inspect before conversion.")

    # Avoid retaining hashes that purportedly describe the new weights.
    for key in ("sshs_model_hash", "sshs_legacy_hash"):
        if key in metadata:
            metadata["original_" + key] = metadata.pop(key)
    metadata["conversion"] = "Split Qwen 2.1 fused gate_up LoRA: duplicate A; split B rows [gate;up]. No numeric rescaling."
    metadata["source_sha256"] = sha256(source)
    save_file(converted, str(destination), metadata=metadata)

    # Verify the actual saved copy, not merely the in-memory conversion.
    saved = load_file(str(destination), device="cpu")
    unaffected = 0
    for key, value in original.items():
        if ".img_mlp.gate_up." not in key:
            if not torch.equal(value, saved[key]):
                raise AssertionError(f"Unchanged tensor differs: {key}")
            unaffected += 1
    for old_prefix, public_prefix in split_modules:
        gate = public_prefix.replace(".gate_up", ".gate_layer")
        up = public_prefix.replace(".gate_up", ".proj")
        a = original[old_prefix + ".lora_A.weight"]
        b = original[old_prefix + ".lora_B.weight"]
        assert torch.equal(a, saved[gate + ".lora_A.weight"])
        assert torch.equal(a, saved[up + ".lora_A.weight"])
        assert torch.equal(b, torch.cat([saved[gate + ".lora_B.weight"], saved[up + ".lora_B.weight"]], dim=0))
    report = {
        "source": str(source),
        "destination": str(destination),
        "source_sha256": metadata["source_sha256"],
        "destination_sha256": sha256(destination),
        "original_tensor_count": len(original),
        "converted_tensor_count": len(saved),
        "split_modules": len(split_modules),
        "unchanged_tensors_verified": unaffected,
        "exact_split_reconstruction_verified": True,
        "numeric_rescaling": False,
        "device": "cpu",
    }
    destination.with_suffix(".conversion.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
