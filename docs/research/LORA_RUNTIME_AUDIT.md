# Personal LoRA runtime audit

Audit date: 2026-09-26. This note distinguishes a successful loader call from a measured effect on inference. The source audit and isolated physical diagnostic below used CPU only and did not modify the active server. A separate [live sampler investigation](LORA_INVESTIGATION.md) checks the active GPU inference path.

## What is and is not established

The custom node prints **“Trained identity adapter applied” unconditionally after the loader returns**. `comfy.sd.load_lora_for_models` can return a clone even if no requested patch matched. That status message is not sufficient proof of application, and nonzero adapter tensors are not sufficient proof that a forward pass consumed them.

The physical CPU diagnostic now confirms all 128 intended key/shape mappings, all 128 returned patch matches, and a nonzero effect through one real quantized linear layer. The separate live sampler probe also measured a repeatable difference with personal LoRA enabled. The evidence rules out a complete no-op in these tested paths; it does not establish good identity learning or convergence.

Two local adapters audited on CPU each contained 256 finite, nonzero tensors: 128 rank-16 attention LoRA A/B pairs. The 80-step adapter's B-matrix RMS was approximately 0.001087; the 400-step adapter's was 0.002594. Files had distinct hashes. Both declared rank 16 and alpha 16. This confirms distinct, nonzero saved parameters, not their usefulness for identity or their effect in the running image workflow.

## Installed source and revisions

Comfy root on the audited machine:

```text
C:/Users/maxvl/AppData/Local/Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI
```

ComfyUI revision: `8ff6dc384ba5c410266b40e137799e049459d4f2`.
ComfyUI-GGUF revision: `6ea2651e7df66d7585f6ffee804b20e92fb38b8a`.
The audited core files and GGUF `nodes.py`, `ops.py`, `dequant.py` and `loader.py` showed no local Git modifications. **GGUF `tools/convert.py` is locally modified**, as detailed below; the entire plugin must not be described as unmodified. The installed `custom_nodes/ComfyUI-UniversalIdentityLocal/__init__.py` matched the repository-authored `comfy-identity/universal_identity/__init__.py` by SHA-256.

The installed loader SHA-256 is `ffff39d209ce22654e67378b80c6a3ca3d349cf8f00ec267085bd16e194730c2`. Its allowlist contains `qwen_image`, not `qwen_image21`. The actual base GGUF has 297 tensors and **no `general.*` metadata**, including no architecture or source tag. It therefore enters the loader's missing-architecture compatibility branch, which imports `tools/convert.py`. That file adds `ModelQwenImage21`, detects `txt_in.text_norm.weight`, `modulation.1.weight`, `transformer_blocks.0.attn.norm_q.weight`, `transformer_blocks.0.img_mlp.proj.weight`, `img_in.weight` and `proj_out.weight`, and returns compatibility architecture `qwen_image`. Comfy's own `comfy/model_detection.py:962` then recognizes the 2.1 model from tensor keys and shapes. This local adaptation explains why this file loads despite the city96 allowlist difference; it does not establish that an arbitrary author's `qwen_image21`-tagged GGUF would load. The modified detector SHA-256 is `b6b2477600d8e19341aa5ebee2c916afa2156d80cdbb5013fb13abc4ac11a20f`. The original base download source remains unrecorded.

All file locations below are relative to that exact Comfy root. Upstream links are pinned to the locally inspected revisions.

| Stage | Installed location | Finding |
|---|---|---|
| Identity node | `custom_nodes/ComfyUI-UniversalIdentityLocal/__init__.py`, `UniversalIdentityProfile.apply_identity` | Loads the selected adapter and passes model strength 0.8. Does not assert matched patch count. |
| Loader | [`comfy/sd.py:106`](https://github.com/Comfy-Org/ComfyUI/blob/8ff6dc384ba5c410266b40e137799e049459d4f2/comfy/sd.py#L106) | Builds a key map, converts/loads LoRA entries, clones the input patcher and calls `add_patches`. Unmatched loaded patches emit a warning, but return is still a patcher. |
| Key mapping | [`comfy/lora.py:326`](https://github.com/Comfy-Org/ComfyUI/blob/8ff6dc384ba5c410266b40e137799e049459d4f2/comfy/lora.py#L326) | The Qwen branch maps `transformer.transformer_blocks.*.attn.*` to `diffusion_model.transformer_blocks.*.attn.*.weight`. QwenImage21 subclasses QwenImage. |
| Adapter decoding | [`comfy/weight_adapter/lora.py:145`](https://github.com/Comfy-Org/ComfyUI/blob/8ff6dc384ba5c410266b40e137799e049459d4f2/comfy/weight_adapter/lora.py#L145) | Recognizes `.lora_B.weight` and `.lora_A.weight` as a pair. Saved adapters target `to_q`, `to_k`, `to_v` and `to_out.0`. |
| Patch registration | [`comfy/model_patcher.py:843`](https://github.com/Comfy-Org/ComfyUI/blob/8ff6dc384ba5c410266b40e137799e049459d4f2/comfy/model_patcher.py#L843) | Registers only keys present in the model state dictionary; returns the matched keys. Appends `(strength_patch, adapter, strength_model, offset, function)` and changes `patches_uuid`. |
| GGUF model creation | [`custom_nodes/ComfyUI-GGUF/nodes.py:148`](https://github.com/city96/ComfyUI-GGUF/blob/6ea2651e7df66d7585f6ffee804b20e92fb38b8a/nodes.py#L148) | Creates the denoiser using `GGMLOps` and converts the model patcher to `GGUFModelPatcher`. |
| GGUF patch attachment | [`custom_nodes/ComfyUI-GGUF/nodes.py:38`](https://github.com/city96/ComfyUI-GGUF/blob/6ea2651e7df66d7585f6ffee804b20e92fb38b8a/nodes.py#L38) | For quantized weights, stores patch tuples on `weight.patches` instead of permanently modifying compressed bytes. `load` forces weight patch attachment even in low-VRAM mode. |
| GGUF forward | [`custom_nodes/ComfyUI-GGUF/ops.py`](https://github.com/city96/ComfyUI-GGUF/blob/6ea2651e7df66d7585f6ffee804b20e92fb38b8a/ops.py) | `GGMLLayer.get_weight` gathers attached patches, dequantizes and calls `comfy.lora.calculate_weight`. `GGMLOps.Linear.forward_ggml_cast_weights` uses that returned weight in `torch.nn.functional.linear`. |
| LoRA arithmetic | [`comfy/weight_adapter/lora.py:224`](https://github.com/Comfy-Org/ComfyUI/blob/8ff6dc384ba5c410266b40e137799e049459d4f2/comfy/weight_adapter/lora.py#L224) | Computes the up/down product, scales it, casts the delta to the weight dtype and adds it. With no per-key alpha tensor, alpha defaults to 1; for these rank-16/alpha-16 adapters that matches training's alpha/rank ratio. Errors here are logged and the existing weight can be returned. |

## Cache and graph behavior

The current graph routes the first denoising pass as:

```text
3: Q8_0 base
  → 4: head-swap LoRA, strength 0.65
  → 14: personal identity LoRA, strength 0.8
  → 5: Qwen KV-cache settings
  → 9: first sampler
```

The realism pass deliberately starts from the base model separately: `3 → 19` (realism LoRA, 0.2) `→ 20 → 22` (denoise 0.04). The personal adapter is not attached directly to that second sampler. Inspect the first-pass output as well as the final composite when evaluating identity effects.

Profile ID, adapter enable/strength and reference index are normal node inputs. The identity node's `IS_CHANGED` also includes profile, adapter and reference modification times. This is consistent with node invalidation, but is still not a direct measurement of patched forward output.

QwenImage21's [`current_patcher` setter](https://github.com/Comfy-Org/ComfyUI/blob/8ff6dc384ba5c410266b40e137799e049459d4f2/comfy/model_base.py#L2660) resets the prefix KV cache at patcher `pre_run`/cleanup. The prefix cache is intended to live within one sampling run. The cache's prompt/reference key alone does not encode LoRA weights; the per-run reset is therefore relevant. No cross-run cache bug was established by this source inspection.

## Physical diagnostic and measured results

Reproducible script: [`audit_lora_cpu.py`](../../comfy-identity/diagnostics/audit_lora_cpu.py). Local numeric report: `.local/lora-runtime-cpu-audit.json` (ignored). It uses a metadata-only model skeleton for the other target keys and one actual 4096×4096 Q8_0 tensor, block 0 `attn.to_q.weight`. The installed loader returned 128 matched personal patch keys on every call; all 128 A/B pairs also matched the actual GGUF names and dimensions. The selected layer included the head-swap adapter at 0.65 before the personal adapter.

| Forward dtype | Personal strength | Selected weights changed | Output delta RMS |
|---|---:|---:|---:|
| FP32 | 0.8 | 99.999% | 0.0109888 |
| FP16 | 0.8 | 93.945% | 0.0110426 |
| BF16 | 0.8 | 60.087% | 0.0132024 |
| FP32 | 1.6 | 100.000% | 0.0219776 |
| BF16 | 1.6 | 77.223% | 0.0233318 |
| FP32 | 4.0 | 100.000% | 0.0549441 |
| BF16 | 4.0 | 90.384% | 0.0555882 |

All 12 combinations (three dtypes × strengths 0/0.8/1.6/4) were finite and matched an independent `baseline + strength × B @ A` calculation with **zero maximum weight and output error**, including matching dtype rounding. These are assertions in the script, not just printed measurements. Zero strength was exactly unchanged for all dtypes. BF16 rounding removes some individual changes, but does not remove the adapter's layer output effect.

The unpatched dequantized tensor was also compared with its corresponding BF16 tensor from the original local Diffusers training checkpoint. Relative L2 error was **0.005760815 (0.576%)** and cosine similarity **0.999983408**, accumulated in FP64. That is consistent with a closely matching quantized tensor, rather than an unrelated base tensor. It is **not** a checksum or provenance verification of the whole model. A first FP32 cosine reduction was numerically invalid for the 16.8-million-element vector; the published metric uses chunked FP64 accumulation.

The script forces CPU mode and blocks CUDA initialization. Comfy's unrelated `comfy_kitchen` GPU attention availability probe is disabled only inside the diagnostic process; it is not part of the tested linear/LoRA path. The successful diagnostic reports `cudaInitialized: false`. No installed production source was edited.

The separate live first-sampler-step control uses the real server and unchanged 80-step schedule, fixed seed/prompt/references/head-swap settings, strengths 0/0.8/4 and a repeated zero control. The zero repeat was bit-identical, while 0.8 and 4 changed the latent output. Its detailed procedure and results belong in the [live investigation](LORA_INVESTIGATION.md), complementing rather than replacing this CPU check.

Diagnostic procedure:

Run a separate, explicitly CPU-only process against the installed source. Do not instrument or restart the active Comfy server, and do not construct the full denoiser.

1. Read one real Q8_0 attention tensor, for example block 0 `attn.to_q.weight`, from the configured GGUF model, preserving its quantization type and original shape.
2. Create a minimal model containing that `GGMLOps.Linear` and the exact model state-dictionary key. Use the installed LoRA mapping, `load_lora_for_models` and `GGUFModelPatcher` rather than independently recreating their arithmetic as the primary test.
3. Record the mapped key, `add_patches` matched count, patch strength and attached `weight.patches` before executing forward. A one-layer diagnostic should match exactly one A/B pair; a separate metadata-only full-key-map audit should account for all 128 intended pairs.
4. Run the same fixed CPU input at personal strengths 0, 0.8 and 1.6. Compare returned dequantized weights and actual linear outputs. Assert the personal patch changes output, produces finite values, and approximately follows the expected strength relationship within dtype rounding.
5. Independently calculate the expected `B @ A` delta as a cross-check. For these adapters its scale is `strength × 16/16`. Include the same head-swap patch first when testing the pipeline's exact patch ordering.
6. Repeat relevant weight/activation dtypes (float32 as an arithmetic reference, then the actual fp16/bf16 inference dtype). This matters because the implementation casts the computed delta before adding it; tiny changes can be rounded away. Do not extrapolate from a float32 result alone.
7. Save only counts, dtypes, shapes, finite checks and numerical norms/errors. Do not version model weights, user photos, or the generated private images.

A passed one-layer check demonstrates the installed mechanism can consume this adapter for that layer. It is stronger than a log message, but does not itself prove every target layer in a prior production render was patched or that the learned identity is useful.

## Controls for image comparisons

To isolate personal adapter weights, use the same base/garment/reference pixels, seed, head-swap strength, generation settings and **exact identity instruction text**. The custom node normally emits a different instruction when `use_trained_identity` is false; pinning the trained instruction in both cases avoids changing prompt and weights together. A separate reference-only product test may intentionally remove the trained trigger, but answers a different question.

The 20/40/80-step comparisons should also share captions and trigger, rather than comparing independently configured profiles and attributing every difference to training length. Similar outputs on one reference-driven example do not establish convergence or justify claiming the personal LoRA was physically applied.
