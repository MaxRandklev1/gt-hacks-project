"""Check isolated training dependencies; GPU arithmetic is explicitly opt-in."""
import argparse
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu-smoke", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"

    import torch
    import torchvision
    import bitsandbytes as bnb
    from transformers import Qwen3VLForConditionalGeneration, Qwen3VLProcessor
    from diffusers import (
        AutoencoderKLQwenImage21,
        BitsAndBytesConfig,
        FlowMatchEulerDiscreteScheduler,
        QwenImage21Pipeline,
        QwenImage21Transformer2DModel,
    )
    from diffusers.training_utils import generate_aspect_ratio_buckets, offload_models
    from accelerate import Accelerator
    from peft import LoraConfig, prepare_model_for_kbit_training
    import datasets

    packages = [
        "torch", "torchvision", "diffusers", "transformers", "accelerate",
        "peft", "datasets", "bitsandbytes", "Pillow", "numpy", "safetensors",
        "huggingface_hub",
    ]
    result = {
        "python": sys.version,
        "executable": sys.executable,
        "base_python": sys.base_prefix,
        "platform": platform.platform(),
        "packages": {p: importlib.metadata.version(p) for p in packages},
        "qwen21_imports": "passed",
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "torch_cuda_architectures": torch.cuda.get_arch_list(),
        "gpu_smoke": "not requested",
    }
    if result["cuda_available"]:
        result["gpu_name"] = torch.cuda.get_device_name(0)
        result["gpu_compute_capability"] = list(torch.cuda.get_device_capability(0))

    if args.gpu_smoke:
        if not result["cuda_available"]:
            raise RuntimeError("CUDA is unavailable")
        torch.manual_seed(42)
        layer = bnb.nn.Linear4bit(
            512, 512, bias=False, compute_dtype=torch.bfloat16,
            compress_statistics=True, quant_type="nf4",
        ).requires_grad_(False).to("cuda")
        lora_a = torch.nn.Parameter(torch.randn(16, 512, device="cuda") * 0.01)
        lora_b = torch.nn.Parameter(torch.zeros(512, 16, device="cuda"))
        optimizer = bnb.optim.AdamW8bit([lora_a, lora_b], lr=1e-3)
        losses = []
        for _ in range(2):
            x = torch.randn(2, 512, device="cuda", dtype=torch.bfloat16)
            prediction = layer(x).float() + (x.float() @ lora_a.T) @ lora_b.T
            loss = (prediction - 0.1).square().mean()
            loss.backward()
            assert torch.isfinite(loss), "Non-finite test loss"
            assert lora_b.grad is not None and torch.isfinite(lora_b.grad).all()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            losses.append(float(loss.detach().cpu()))
        assert torch.count_nonzero(lora_b).item() > 0, "Adapter weights did not update"
        torch.cuda.synchronize()
        result["gpu_smoke"] = "passed: NF4 forward/backward and AdamW8bit adapter update"
        result["gpu_smoke_losses"] = losses
        result["gpu_smoke_max_allocated_bytes"] = torch.cuda.max_memory_allocated()

    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
