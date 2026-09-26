# Isolated Qwen Image 2.1 identity trainer

Adapted from the official Diffusers DreamBooth trainer at commit
`e0abab83b5df05de9e7abd788643c1a7c1e42e28`. The original Apache-2.0 source is
preserved in `upstream_train_dreambooth_lora_qwenimage21.py`; hashes and provenance
are in `source_metadata.json`. `build_local_trainer.py` reproducibly applies the
reviewed adaptation and checks Python syntax without importing ML libraries.

## Memory strategy

1. Load the frozen BF16 VAE and Qwen3-VL encoder. The tested launch below uses
   `--text_encoder_4bit` to load the encoder in NF4 on GPU.
2. Cache per-image captions on CPU and encode each image through the VAE on GPU.
3. Release the VAE, encoder, and text-encoding pipeline before loading the DiT.
4. Load the frozen DiT in NF4 and train only FP32 LoRA adapter parameters.
5. Move only the current cached image/embedding batch to the GPU.

Preprocessing finishes and releases the encoder before the DiT is loaded, so
both large models never share VRAM. Quantized text models are not moved with
`.to()`; cached embeddings are detached to CPU. The NF4 encoder path passed both
the real encoder smoke test and the complete two-update training check on this
machine. Its caption embeddings can differ slightly from BF16. Omitting
`--text_encoder_4bit` selects the unmeasured BF16 CPU encoder path.

BF16 encoder weights plus the VAE alone are about 18.2 GB in decimal units.
Transient tensors, Python and loading buffers add to that; a rough CPU-process
planning allowance is 24–28 GB, not a measured guarantee. If Windows has only
17–18 GB free, use the optional quantized encoder path instead of depending on
paging.

Caches are currently per-process; starting another training process repeats
preprocessing. The existing ComfyUI Q8 GGUF and convrot encoder are inference
files; this trainer reads a separate local Diffusers checkpoint directory.

Measured on 2026-09-26 with the RTX 5080 16GB, 32GB system RAM, and the five-image
dataset:

- The separate real Qwen3-VL NF4 encoder smoke test passed in 12.5 seconds with
  6.10 GiB peak CUDA allocation (`../text-encoder-health.json`).
- The complete two-update check passed in 27.3 seconds, including preprocessing,
  model loading, both optimizer updates, and saved-adapter verification. Peak
  CUDA allocation across that process was 7.12 GiB. The highest sampled process
  RSS was 2.62 GiB; this is a sample, not a measured process high-water mark.
- All 128 LoRA-B tensors had finite, nonzero gradients and changed on both
  updates. All 128 saved LoRA-B tensors were finite and nonzero. The adapter has
  16,777,216 trainable parameters. Evidence is in
  `../outputs/healthcheck/training_health.json`.

These results verify the short training path and its measured memory use.

The separate **400-update pilot** then completed successfully on 2026-09-26:

- Total elapsed time through saved-adapter verification was **284.135 seconds**
  (4 minutes 44 seconds), including preprocessing and model loading.
- Peak CUDA allocation remained **7.12 GiB**. Highest sampled process RSS was
  **2.62 GiB**, again a sample rather than a process high-water mark.
- All 42 scheduled health checks passed through update 400. All 128 LoRA-B
  tensors had nonzero gradients and changed at every check; the final saved
  adapter passed the finite/nonzero verification.
- The final adapter is `../outputs/jon-pilot/pytorch_lora_weights.safetensors`,
  **67,143,792 bytes**. Checkpoints at 100, 200, 300, and 400 updates are retained.
  The run's `training_health.json` and `TRAINING.md` record results and provenance;
  the original console log is `../jon-pilot.log`.

Visual sampling and the BFS comparison are still being evaluated. Successful
training and nonzero weights do not establish likeness or an optimal checkpoint.

## Local-only behavior

Hub and dataset offline mode are forced before the ML imports. The model and
dataset arguments must be local paths. Uploads, remote trackers, validation image
generation, and automatic checkpoint deletion are rejected. Logs, checkpoints,
and `training_health.json` remain in the chosen output directory. Model downloads
are a separate explicitly managed step, never initiated by this script.

## Commands

Run from `comfy-identity/training`. The reusable launcher uses the existing
`.venv`, `models/Qwen-Image-2.1`, and `dataset` in that directory:

```powershell
.\run-jon-training.ps1
# Choose a fresh output directory or change the training length:
.\run-jon-training.ps1 -OutputDir .\outputs\jon-next -MaxSteps 400
```

The default is 400 updates with checkpoints every 100 updates in
`outputs/jon-pilot`. The launcher refuses any nonempty output directory, including
an active or completed run; use a fresh directory for another run. Relative
output paths are resolved from the training directory regardless of the current
working directory. It writes console output to the run's `training.log` and
returns an error if training fails. It does not resume or delete checkpoints.

Both launcher and trainer set offline/telemetry-disable environment variables;
the trainer also requires local model and dataset paths and rejects uploads and
remote trackers. The launcher performs no downloads, installs, or uploads. This
is application-level offline behavior, not a Windows network firewall rule.

For a two-update diagnostic in a **fresh** directory, the equivalent direct
command is below. The complete local checkpoint contains `transformer`,
`text_encoder`, `vae`, `processor`, `scheduler`, and `model_index.json`.

```powershell
& .\.venv\Scripts\python.exe .\trainer\train_identity_qwen21.py `
  --pretrained_model_name_or_path .\models\Qwen-Image-2.1 `
  --dataset_name .\dataset --image_column image --caption_column text `
  --instance_prompt 'a photo of j0n_person, a man' `
  --output_dir .\outputs\healthcheck-new `
  --mixed_precision bf16 --bnb_quantization_config_path .\trainer\nf4.json `
  --text_encoder_4bit `
  --offload --cache_latents --gradient_checkpointing --use_8bit_adam `
  --train_batch_size 1 --gradient_accumulation_steps 1 `
  --resolution 512 --use_aspect_ratio_buckets --aspect_ratio_buckets '512,384;512,448' `
  --center_crop --rank 16 --lora_alpha 16 --learning_rate 1e-4 `
  --lr_scheduler constant --lr_warmup_steps 0 --max_train_steps 2 `
  --checkpointing_steps 1 --dataloader_num_workers 0 --seed 42 `
  --report_to none --skip_final_inference
```

Buckets are **height,width**. Both portrait shapes are divisible by 32 and use
less area than 512×512. Aspect-ratio matching reduces crop loss; inspect each
prepared crop before training. The dataset's `metadata.jsonl` supplies captions.

The launcher uses these same training flags, with the requested output directory
and update count and `--checkpointing_steps 100`. The 400-update configuration is
a pilot; it does not establish an optimal training length. Compare saved
checkpoints for identity, expression, and scene adherence before choosing one
for the BFS workflow.

## Health and verification

At updates 1, 2, and every 10 updates, the trainer checks finite gradients,
nonzero LoRA-B gradients, finite LoRA-B weights, and changes since the previous
check. It verifies the saved safetensors adapter has finite, nonzero LoRA-B
weights. GPU peak allocation and optional process/system RAM metrics are logged.
Zero LoRA-A gradients in the first update are not treated as failure because
LoRA-B initializes at zero. Checkpoint differences prove learning activity, not
good likeness.

```powershell
& .\.venv\Scripts\python.exe .\trainer\build_local_trainer.py
& .\.venv\Scripts\python.exe .\trainer\test_local_health.py
& .\.venv\Scripts\python.exe .\trainer\train_identity_qwen21.py --help
```

The tests use tiny CPU tensors and synthetic gradients only. They never load a
model, photos, or GPU tensors. A person LoRA trained through this path shares the
Qwen Image 2.1 architecture with editing/BFS; its usefulness in that combination
still requires a fixed-input comparison against BFS alone.

Verified on 2026-09-26 in the isolated environment: all four health tests pass;
trainer imports and `--help` pass; local dataset loading finds five image/text
rows; the actual DreamBooth dataset produces three 4×512×384 tensors and two
4×512×448 tensors with the identity token in every caption. Independent source
review found no critical device, shape, or model-lifetime issue for the documented
flags. Windows console output is explicitly UTF-8 to avoid an upstream help-text
encoding failure. The real encoder and complete two-update results are recorded
above. Keep `--lr_scheduler constant --lr_warmup_steps 0`: a scheduler with an
initial zero learning rate would trip the unchanged-weight diagnostic before
learning starts.
