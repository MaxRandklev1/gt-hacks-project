"""Reproduce the isolated low-memory adaptation from a pinned upstream source.

This script only transforms text and compiles Python syntax. It never imports ML
libraries, downloads models, or starts a training job.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "upstream_train_dreambooth_lora_qwenimage21.py"
TARGET = ROOT / "train_identity_qwen21.py"


def replace_once(text, old, new):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match, got {count}: {old[:100]!r}")
    return text.replace(old, new, 1)


def main():
    source = SOURCE.read_text(encoding="utf-8")
    expected = json.loads((ROOT / "source_metadata.json").read_text())["script_sha256"]
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Upstream source changed; review and update metadata first.")

    # Keep the upstream training objective, dataset, sampler, save/load hooks and
    # model architecture. Only change setup order, memory movement and reporting.
    source = replace_once(source, "import os\n", '''import os
# This local-only trainer must never upload data, models, or experiment logs.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["WANDB_DISABLED"] = "true"
os.environ["WANDB_MODE"] = "disabled"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
import sys
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from local_health import TrainingHealth
''')
    source = replace_once(source, '        default="tensorboard",', '        default="none",')
    source = replace_once(source, '    parser.add_argument(\n        "--cache_latents",', '''    parser.add_argument(
        "--text_encoder_4bit", action="store_true",
        help="Precompute frozen text embeddings with NF4 Qwen3-VL on GPU before loading the DiT."
    )
    parser.add_argument(
        "--cache_latents",''')
    source = replace_once(source, "def main(args):\n", '''def main(args):
    if args.push_to_hub or args.hub_token or args.hub_model_id:
        raise ValueError("Hub uploads are disabled in this local-only trainer.")
    if args.report_to not in (None, "none"):
        raise ValueError("External tracking is disabled; use --report_to none.")
    if not Path(args.pretrained_model_name_or_path).is_dir():
        raise ValueError("Supply a complete local Diffusers model directory.")
    if args.dataset_name is not None and not Path(args.dataset_name).exists():
        raise ValueError("Only local datasets are accepted.")
    if not args.cache_latents or not args.offload:
        raise ValueError("This 16GB adaptation requires --cache_latents --offload.")
    if args.mixed_precision != "bf16" or not args.bnb_quantization_config_path:
        raise ValueError("Use --mixed_precision bf16 and the supplied NF4 config.")
    if args.with_prior_preservation:
        raise ValueError("Automatic class-image generation is disabled in this local adaptation.")
    if args.validation_prompt or args.final_validation_prompt or not args.skip_final_inference:
        raise ValueError("Validate saved LoRAs separately; use --skip_final_inference without validation prompts.")
    if args.checkpoints_total_limit is not None:
        raise ValueError("Automatic checkpoint deletion is disabled; omit --checkpoints_total_limit.")
    if args.cache_dir is None:
        args.cache_dir = str(Path(args.output_dir) / "dataset-cache")
    args.report_to = None
    health = TrainingHealth(Path(args.output_dir))
''')
    source = replace_once(source, "        log_with=args.report_to,", "        log_with=None,")
    source = replace_once(source, "    # Disable AMP for MPS.\n", '''    if accelerator.num_processes != 1:
        raise ValueError("This adaptation is reviewed for a single GPU only.")
    # Disable AMP for MPS.
''')
    # Separate preprocessing from DiT construction. No BF16 encoder and DiT
    # loading peak overlap, and the BF16 encoder never enters CUDA memory.
    start = source.index("    quantization_config = None\n", source.index("def main(args):"))
    split = source.index("    # Resolve the bucketing mode.", start)
    end = source.index("    # Scheduler and math around the number of training steps.", split)
    transformer_setup = source[start:split]
    preprocessing = source[split:end]
    encoder_setup_start = transformer_setup.index("    # Initialize a text encoding pipeline")
    encoder_setup_end = transformer_setup.index("    if args.gradient_checkpointing:", encoder_setup_start)
    encoder_setup = transformer_setup[encoder_setup_start:encoder_setup_end]
    transformer_setup = transformer_setup[:encoder_setup_start] + transformer_setup[encoder_setup_end:]
    transformer_setup = replace_once(transformer_setup, "    vae.requires_grad_(False)\n    text_encoder.requires_grad_(False)\n", "")
    movement_start = transformer_setup.index("    to_kwargs = ")
    movement_end = transformer_setup.index("    # we never offload the transformer", movement_start)
    transformer_setup = transformer_setup[:movement_start] + transformer_setup[movement_end:]
    transformer_setup = replace_once(
        transformer_setup,
        '        quantization_config = BitsAndBytesConfig(**config_kwargs)',
        '''        if not config_kwargs.get("load_in_4bit") or config_kwargs.get("bnb_4bit_quant_type") != "nf4":
            raise ValueError("The isolated trainer requires NF4 DiT quantization.")
        quantization_config = BitsAndBytesConfig(**config_kwargs)''',
    )
    # Ensure small trainable adapter weights retain optimizer precision.
    transformer_setup = replace_once(
        transformer_setup,
        "    transformer.add_adapter(transformer_lora_config)\n",
        "    transformer.add_adapter(transformer_lora_config)\n    cast_training_params(transformer, dtype=torch.float32)\n",
    )
    preprocessing = preprocessing.replace(
        "offload_models(text_encoding_pipeline, device=accelerator.device, offload=args.offload)",
        'offload_models(text_encoding_pipeline, device="cpu", offload=False)',
    )
    preprocessing = replace_once(preprocessing, '    text_encoding_pipeline = text_encoding_pipeline.to("cpu")\n', '''    if not args.text_encoder_4bit:
        text_encoding_pipeline = text_encoding_pipeline.to("cpu")
''')
    preprocessing = replace_once(preprocessing, "    del text_encoder\n    free_memory()\n", "    del text_encoder, text_encoding_pipeline\n    free_memory()\n    health.memory_event(\"preprocessing_complete\")\n")
    # Latents and embeddings are cached on host memory, then only the current
    # batch moves to CUDA. This matters if the collection grows beyond ten photos.
    preprocessing = preprocessing.replace("instance_latents[i : i + 1]", "instance_latents[i : i + 1].detach().cpu()")
    preprocessing = preprocessing.replace("class_latents[i : i + 1]", "class_latents[i : i + 1].detach().cpu()")
    encoder_prep = '''    vae.requires_grad_(False)
    text_encoder.requires_grad_(False)
    vae.to(device="cpu", dtype=weight_dtype)
    if not args.text_encoder_4bit:
        text_encoder.to(device="cpu", dtype=weight_dtype)
    health.memory_event("encoder_loaded_nf4_on_gpu" if args.text_encoder_4bit else "encoder_loaded_on_cpu")
'''
    source = source[:start] + encoder_prep + encoder_setup + preprocessing + transformer_setup + source[end:]
    # Avoid an FP32 VAE allocation during initial load (the official checkpoint
    # is FP32 on disk; its frozen training encoder is stable in BF16).
    source = replace_once(
        source,
        '        subfolder="vae",\n        revision=args.revision,\n        variant=args.variant,\n    )',
        '        subfolder="vae",\n        revision=args.revision,\n        variant=args.variant,\n        torch_dtype=weight_dtype,\n    )',
    )
    source = replace_once(
        source,
        '        args.pretrained_model_name_or_path, subfolder="text_encoder", revision=args.revision, torch_dtype=weight_dtype\n',
        '        args.pretrained_model_name_or_path, subfolder="text_encoder", revision=args.revision,\n        torch_dtype=weight_dtype, attn_implementation="sdpa", low_cpu_mem_usage=True, **encoder_load_kwargs\n',
    )
    source = replace_once(source, "    text_encoder = Qwen3VLForConditionalGeneration.from_pretrained(\n", '''    encoder_load_kwargs = {}
    if args.text_encoder_4bit:
        if accelerator.device.type != "cuda":
            raise ValueError("--text_encoder_4bit requires a CUDA GPU.")
        encoder_load_kwargs = {
            "quantization_config": transformers.BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=weight_dtype,
            ),
            "device_map": {"": str(accelerator.device)},
        }
    text_encoder = Qwen3VLForConditionalGeneration.from_pretrained(
''')
    source = replace_once(source, "        return prompt_embeds, prompt_embeds_mask, image_pad_mask\n\n    # If no type", '''        prompt_embeds = prompt_embeds.detach().cpu()
        if prompt_embeds_mask is not None:
            prompt_embeds_mask = prompt_embeds_mask.detach().cpu()
        if image_pad_mask is not None:
            image_pad_mask = image_pad_mask.detach().cpu()
        return prompt_embeds, prompt_embeds_mask, image_pad_mask

    # If no type''')
    source = replace_once(
        source,
        "                prompt_embeds, prompt_embeds_mask = concat_prompt_embedding_batches(*prompt_pairs)\n",
        '''                prompt_embeds, prompt_embeds_mask = concat_prompt_embedding_batches(*prompt_pairs)
                prompt_embeds = prompt_embeds.to(accelerator.device, dtype=weight_dtype)
                if prompt_embeds_mask is not None:
                    prompt_embeds_mask = prompt_embeds_mask.to(accelerator.device)
''',
    )
    source = replace_once(source, "                model_input = (model_input - latents_mean) * latents_std\n", "                model_input = model_input.to(accelerator.device, dtype=weight_dtype)\n                model_input = (model_input - latents_mean) * latents_std\n")
    source = replace_once(source, '    progress_bar = tqdm(\n        range(0, args.max_train_steps),', '    health.start(transformer, global_step)\n    progress_bar = tqdm(\n        range(0, args.max_train_steps),')
    source = replace_once(source, "                accelerator.backward(loss)\n", '''                if not torch.isfinite(loss.detach()).all():
                    raise FloatingPointError("Nonfinite training loss; stopping before optimizer update.")
                accelerator.backward(loss)
                if accelerator.sync_gradients:
                    health.check_gradients(transformer, global_step + 1)
''')
    source = replace_once(source, "                optimizer.step()\n                lr_scheduler.step()", '''                optimizer.step()
                if accelerator.sync_gradients:
                    health.after_step(transformer, global_step + 1, float(loss.detach()))
                lr_scheduler.step()''')
    source = replace_once(source, "    accelerator.end_training()\n", "    health.finish(Path(args.output_dir) / \"pytorch_lora_weights.safetensors\", global_step)\n    accelerator.end_training()\n")
    ast.parse(source, filename=str(TARGET))
    TARGET.write_text(source, encoding="utf-8", newline="\n")
    metadata = json.loads((ROOT / "source_metadata.json").read_text())
    metadata["adapted_script_sha256"] = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    metadata["adaptations"] = [
        "BF16 CPU text preprocessing, or optional NF4 GPU text preprocessing, before loading the NF4 DiT",
        "Free text pipeline and encoder references before DiT allocation",
        "Cached latents on CPU, current batch transferred to GPU",
        "Local files and offline execution required; no external trackers or uploads",
        "Finite gradient and LoRA-B change checks with saved adapter verification",
        "No automatic validation generation or checkpoint deletion",
    ]
    (ROOT / "source_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET}; syntax checked; no ML code executed.")


if __name__ == "__main__":
    main()
