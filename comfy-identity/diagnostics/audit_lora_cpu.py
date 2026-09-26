"""Exercise installed Comfy/GGUF LoRA registration and one real linear layer on CPU.

No server connection, GPU initialization, model download or production mutation.
Only one GGUF tensor is copied; other target weights are metadata-only tensors.
The report is numeric and belongs under .local/, not in the public demo assets.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
from pathlib import Path
import sys
import types
from unittest.mock import patch


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root", type=Path, required=True)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--head-adapter", type=Path)
    parser.add_argument("--training-transformer", type=Path, help="Optional local Diffusers transformer directory for one-tensor base comparison.")
    parser.add_argument("--head-strength", type=float, default=0.65)
    parser.add_argument("--target", default="transformer_blocks.0.attn.to_q.weight")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def nested_module(root, dotted, factory):
    parts = dotted.split(".")
    current = root
    for part in parts[:-1]:
        if part not in current._modules:
            current.add_module(part, factory())
        current = current._modules[part]
    return current, parts[-1]


def compare_base_tensors(original, quantized, original_dtype):
    """Accumulate in FP64; full-vector FP32 cosine can exceed 1 at this size."""
    sums = {"original": 0.0, "quantized": 0.0, "difference": 0.0, "dot": 0.0}
    maximum = 0.0
    left, right = original.flatten(), quantized.flatten()
    for start in range(0, left.numel(), 1_048_576):
        a, b = left[start:start + 1_048_576].double(), right[start:start + 1_048_576].double()
        difference = b - a
        sums["original"] += float(a.square().sum())
        sums["quantized"] += float(b.square().sum())
        sums["difference"] += float(difference.square().sum())
        sums["dot"] += float((a * b).sum())
        maximum = max(maximum, float(difference.abs().max()))
    count = left.numel()
    cosine = sums["dot"] / math.sqrt(sums["original"] * sums["quantized"])
    if not -1.000000000001 <= cosine <= 1.000000000001:
        raise AssertionError("Base cosine calculation is invalid.")
    return {"shape": list(original.shape), "originalDtype": original_dtype,
            "relativeL2Error": math.sqrt(sums["difference"] / sums["original"]),
            "cosineSimilarity": cosine, "accumulationDtype": "float64",
            "originalRms": math.sqrt(sums["original"] / count),
            "quantizedRms": math.sqrt(sums["quantized"] / count),
            "differenceRms": math.sqrt(sums["difference"] / count), "maxAbsDifference": maximum,
            "scope": "One unpatched dequantized attention tensor versus the local training checkpoint; not whole-model provenance."}


def main():
    args = arguments()
    if not args.comfy_root.is_dir() or not args.gguf.is_file() or not args.adapter.is_file():
        raise ValueError("Existing installed source, GGUF and adapter files are required.")
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    sys.path.insert(0, str(args.comfy_root.resolve()))
    # Enable the installed Comfy parser before importing modules that select devices.
    sys.argv = [sys.argv[0], "--cpu", "--disable-metadata"]
    import comfy.options
    comfy.options.enable_args_parsing()
    import torch
    torch.set_num_threads(2)
    if torch.cuda.is_initialized():
        raise RuntimeError("The interpreter initialized CUDA before the audit imported Comfy.")
    def forbid_cuda_initialization(*_, **__):
        raise RuntimeError("Blocked an unexpected CUDA initialization in the CPU audit.")
    torch.cuda._lazy_init = forbid_cuda_initialization
    import comfy_kitchen
    # Comfy probes this GPU-only attention capability even with --cpu. The audit
    # runs only a linear layer, so disable the irrelevant capability query in
    # this isolated interpreter, retaining a hard guard against any CUDA use.
    comfy_kitchen.int8_attention_is_available = lambda *_, **__: False
    import comfy.sd
    import comfy.lora
    import comfy.model_base
    import comfy.model_management
    from safetensors import safe_open
    import gguf

    if torch.cuda.is_initialized() or comfy.model_management.get_torch_device().type != "cpu":
        raise RuntimeError(f"Diagnostic must remain CPU-only: configured device={comfy.model_management.get_torch_device()}, cpu flag={comfy.model_management.args.cpu}, initialized={torch.cuda.is_initialized()}.")
    # Load the installed plugin modules under an isolated package namespace;
    # do not execute its registration entry point or start ComfyUI.
    plugin = types.ModuleType("isolated_gguf_audit")
    plugin.__path__ = [str(args.comfy_root / "custom_nodes" / "ComfyUI-GGUF")]
    sys.modules[plugin.__name__] = plugin
    ops = importlib.import_module(plugin.__name__ + ".ops")
    nodes = importlib.import_module(plugin.__name__ + ".nodes")
    loader = importlib.import_module(plugin.__name__ + ".loader")

    reader = gguf.GGUFReader(str(args.gguf))
    tensors = {}
    for item in reader.tensors:
        name = item.name.removeprefix("model.diffusion_model.").removeprefix("diffusion_model.")
        tensors[name] = item
    target = tensors[args.target]
    shape = tuple(loader.get_orig_shape(reader, target.name) or tuple(reversed(target.shape.tolist())))
    if len(shape) != 2 or shape[0] * shape[1] > 32_000_000:
        raise ValueError("Choose one bounded two-dimensional linear weight.")
    with safe_open(str(args.adapter), framework="pt", device="cpu") as handle:
        weights = {key: handle.get_tensor(key) for key in handle.keys()}
        metadata = handle.metadata() or {}
    adapter_metadata = json.loads(metadata.get("lora_adapter_metadata", "{}"))
    rank = adapter_metadata.get("transformer.r")
    alpha = adapter_metadata.get("transformer.lora_alpha")
    training_scale = float(alpha) / float(rank) if alpha is not None and rank is not None else 1.0

    # Build only target-layer names and dimensions. This is not a full denoiser.
    model = comfy.model_base.QwenImage21.__new__(comfy.model_base.QwenImage21)
    torch.nn.Module.__init__(model)
    model.model_config = types.SimpleNamespace(unet_config={})
    model.diffusion_model = torch.nn.Module()
    mappings = []
    for key in sorted(weights):
        if not key.endswith(".lora_A.weight"):
            continue
        prefix = key[:-len(".lora_A.weight")]
        target_name = prefix.removeprefix("transformer.") + ".weight"
        item = tensors.get(target_name)
        b_key = prefix + ".lora_B.weight"
        if item is None or b_key not in weights:
            raise AssertionError("An adapter pair has no corresponding GGUF tensor.")
        actual_shape = tuple(loader.get_orig_shape(reader, item.name) or tuple(reversed(item.shape.tolist())))
        a, b = weights[key], weights[b_key]
        valid = tuple(a.shape) == (b.shape[1], actual_shape[1]) and b.shape[0] == actual_shape[0]
        if not valid:
            raise AssertionError("LoRA A/B dimensions do not match the real GGUF tensor.")
        parent, leaf = nested_module(model, "diffusion_model." + target_name.removesuffix(".weight"), torch.nn.Module)
        if target_name == args.target:
            layer = ops.GGMLOps.Linear(actual_shape[1], actual_shape[0], bias=False, device="cpu")
            tensor = ops.GGMLTensor(torch.from_numpy(item.data.copy()), tensor_type=item.tensor_type,
                                    tensor_shape=torch.Size(actual_shape), patches=[])
            layer.weight = torch.nn.Parameter(tensor, requires_grad=False)
        else:
            layer = torch.nn.Module()
            layer.register_parameter("weight", torch.nn.Parameter(torch.empty(actual_shape, device="meta"), requires_grad=False))
        parent.add_module(leaf, layer)
        mappings.append({"adapterPrefix": prefix, "modelKey": "diffusion_model." + target_name,
                         "shape": list(actual_shape), "rank": int(a.shape[0]), "shapeMatches": valid})

    cpu = torch.device("cpu")
    base = nodes.GGUFModelPatcher(model, cpu, cpu)
    key_map = comfy.lora.model_lora_keys_unet(model, {})
    for item in mappings:
        if key_map.get(item["adapterPrefix"]) != item["modelKey"]:
            raise AssertionError("Installed Comfy key mapping differs from the intended GGUF key.")
    selected_key = "diffusion_model." + args.target
    selected_layer = comfy.utils.get_attr(model, selected_key.removesuffix(".weight"))
    base_comparison = None
    if args.training_transformer:
        index = json.loads((args.training_transformer / "diffusion_pytorch_model.safetensors.index.json").read_text(encoding="utf-8"))
        shard = args.training_transformer / index["weight_map"][args.target]
        with safe_open(str(shard), framework="pt", device="cpu") as handle:
            original_dtype = handle.get_slice(args.target).get_dtype()
            original = handle.get_tensor(args.target).float()
        quantized = selected_layer.get_weight(selected_layer.weight, torch.float32)
        if tuple(original.shape) != tuple(quantized.shape):
            raise AssertionError("Training checkpoint and GGUF target shape differ.")
        base_comparison = compare_base_tensors(original, quantized, original_dtype)
        del original, quantized
    add_calls = []
    original_add = nodes.GGUFModelPatcher.add_patches
    def measured_add(self, patches, *positional, **keywords):
        matched = original_add(self, patches, *positional, **keywords)
        add_calls.append({"requested": len(patches), "matched": len(matched),
                          "strength": positional[0] if positional else keywords.get("strength_patch", 1.0)})
        return matched

    with patch.object(nodes.GGUFModelPatcher, "add_patches", measured_add):
        if args.head_adapter:
            aliases = [alias for alias, key in key_map.items() if key == selected_key]
            with safe_open(str(args.head_adapter), framework="pt", device="cpu") as handle:
                head_weights = {key: handle.get_tensor(key) for key in handle.keys()
                                if any(key.startswith(alias + ".") for alias in aliases)}
            if not head_weights:
                raise AssertionError("Head-swap adapter contains no matching selected-layer weights.")
            base, _ = comfy.sd.load_lora_for_models(base, None, head_weights, args.head_strength, 0)
        cases = []
        a_key = "transformer." + args.target.removesuffix(".weight") + ".lora_A.weight"
        b_key = a_key.replace(".lora_A.weight", ".lora_B.weight")
        delta = (weights[b_key].float() @ weights[a_key].float()) * training_scale
        generator = torch.Generator(device="cpu").manual_seed(20260926)
        inputs = torch.randn((4, shape[1]), generator=generator, device="cpu", dtype=torch.float32)
        for dtype_name in ("float32", "float16", "bfloat16"):
            dtype = getattr(torch, dtype_name)
            selected_layer.weight.patches = []
            base.patch_weight_to_device(selected_key, device_to=cpu)
            baseline_weight = selected_layer.get_weight(selected_layer.weight, dtype).clone()
            baseline_output = selected_layer(inputs.to(dtype)).float()
            for strength in (0.0, 0.8, 1.6, 4.0):
                patched, _ = comfy.sd.load_lora_for_models(base, None, weights, strength, 0)
                # All intended keys must be present, including the head-swap's shared key.
                if not all(item["modelKey"] in patched.patches for item in mappings):
                    raise AssertionError("At least one personal adapter patch did not register.")
                patched.patch_weight_to_device(selected_key, device_to=cpu)
                actual_weight = selected_layer.get_weight(selected_layer.weight, dtype)
                actual_output = selected_layer(inputs.to(dtype)).float()
                expected_weight = baseline_weight + (strength * delta).to(dtype)
                expected_output = torch.nn.functional.linear(inputs.to(dtype), expected_weight).float()
                weight_change = actual_weight.float() - baseline_weight.float()
                output_change = actual_output - baseline_output
                row = {"dtype": dtype_name, "strength": strength, "matchedPersonalKeys": len(mappings),
                       "attachedSelectedLayerPatches": len(patched.patches[selected_key]),
                       "weightChangedFraction": float(torch.count_nonzero(weight_change)) / weight_change.numel(),
                       "weightDeltaRms": float(weight_change.square().mean().sqrt()),
                       "weightExpectedMaxAbsError": float((actual_weight.float() - expected_weight.float()).abs().max()),
                       "outputDeltaRms": float(output_change.square().mean().sqrt()),
                       "outputExpectedMaxAbsError": float((actual_output - expected_output).abs().max()),
                       "allFinite": bool(actual_weight.isfinite().all() and actual_output.isfinite().all())}
                if not row["allFinite"] or (strength > 0 and row["outputDeltaRms"] == 0):
                    raise AssertionError("Selected layer failed to apply a finite nonzero personal adapter effect.")
                if strength == 0 and row["outputDeltaRms"] != 0:
                    raise AssertionError("Zero-strength adapter changed the selected layer output.")
                torch.testing.assert_close(actual_weight.float(), expected_weight.float(), rtol=0, atol=0)
                torch.testing.assert_close(actual_output, expected_output, rtol=0, atol=0)
                cases.append(row)
                print(json.dumps({"case": row}), flush=True)
    if torch.cuda.is_initialized():
        raise AssertionError("CUDA was unexpectedly initialized.")
    report = {"cpuOnly": True, "cudaInitialized": False, "baseTensor": args.target,
              "quantization": getattr(target.tensor_type, "name", str(target.tensor_type)), "shape": list(shape),
              "ggufTensorCount": len(reader.tensors), "ggufArchitectureMetadata": loader.get_field(reader, "general.architecture", str),
              "trainingBaseComparison": base_comparison,
              "personalTensorCount": len(weights), "targetPairCount": len(mappings), "mappings": mappings,
              "rank": rank, "alpha": alpha, "expectedTrainingAlphaOverRank": training_scale,
              "headSwapIncluded": bool(args.head_adapter), "headSwapStrength": args.head_strength if args.head_adapter else None,
              "loaderAddPatchesCalls": add_calls, "cases": cases,
              "limits": "Metadata-only model skeleton plus one real quantized linear tensor. Proves installed key registration and this layer's CPU forward effect; does not inspect the running server, all layers' execution, GPU kernels, denoising, or identity quality."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "matchedPairs": len(mappings), "cpuOnly": True}), flush=True)


if __name__ == "__main__":
    main()
