# Is the personal LoRA actually doing anything?

Investigated September 26, 2026, on the project's running ComfyUI installation. This is a research handoff; the app, onboarding defaults, training settings, and production workflow were not changed.

## Answer supported by the experiment

**The personal LoRA affects the live sampler. It is not completely disconnected or numerically inert in the tested configuration.** A controlled experiment changed only personal-adapter strength and output filenames. Normal strength changed the sampler's latent output; exaggerated strength changed it more. A fresh zero-strength repeat reproduced the original zero-strength output exactly.

This establishes a computational effect, **not useful identity learning**. The near-identical finished faces still need an explanation. The previous suggestion that similar images established that personal training was unnecessary went beyond the evidence. Training mechanics, runtime application, and likeness quality are separate questions.

## First principles

A LoRA changes a targeted base weight approximately as `W' = W + s × (alpha/rank) × B A`. Here `s` is the inference slider. The learned matrices do not have to produce an obvious face change merely because they are nonzero: they may change lighting, texture, or features already supplied by the reference image. The original method freezes base weights and trains added low-rank matrices. [Original LoRA implementation and paper](https://github.com/microsoft/LoRA)

Our first pass has two sources of identity information: an actual reference portrait, and the personal adapter associated with its text trigger. It also has the BFS head-swap adapter. The model can use the portrait even with the personal adapter disabled. Therefore a convincing reference-only result does not demonstrate that the personal adapter learned the person, nor that it failed to load.

## Live sampler experiment

The diagnostic used the running server, installed custom identity node, actual Q8 model, BFS strength 0.65, the same portrait/pose/garment inputs, the same literal identity instruction, seed 42, Euler, CFG 1, and the original 80-step simple schedule. It executed **only the first step** with `KSamplerAdvanced`, retained the remaining noise, and saved the raw latent before VAE decoding, realism processing, masking, or upscaling.

The executor/model cache was cleared before each case. Each execution history reports an empty cached-node list. The sampler executed independently in all four cases. Submitted graphs match after normalizing personal strength and output prefix. This uses the current 80-step personal adapter.

| Personal strength | Relative L2 difference from first zero run | Mean absolute latent difference | Maximum absolute latent difference |
|---:|---:|---:|---:|
| 0, first run | 0 | 0 | 0 |
| 0.8, production value | 0.000305128 | 0.123959 | 1.668877 |
| 4.0, diagnostic stress value | 0.001625971 | 0.651408 | 12.375183 |
| 0, fresh repeat | **0 exactly** | **0 exactly** | **0 exactly** |

All saved arrays were finite and shaped `[1, 64, 64, 64]`. The exaggerated setting is a diagnostic, not a proposed production value. The relative denominator includes substantial first-step noise; these numbers are neither percentages of likeness nor measures of image quality. Nonlinear denoising also means a fivefold strength change need not produce exactly fivefold output change.

This directly rejects a complete no-op for the tested live first step, including the selected profile and stacked BFS path. It does not instrument every layer in every previous image render, prove usefulness over later timesteps, or rule out partial compatibility issues. See the separate [runtime audit](LORA_RUNTIME_AUDIT.md) for layer-level mapping and arithmetic checks.

An independent CPU probe also passed: all **128 intended updates** matched real GGUF keys and shapes and were accepted by the installed patcher. One real quantized attention layer, including the preceding BFS patch, produced exactly the expected patched weight and linear output under FP32, FP16, and BF16 arithmetic. BF16 rounding removed some individual weight changes but did not eliminate the adapter's effect. This strengthens the compatibility evidence without claiming every layer was instrumented inside the live GPU process.

The same unpatched Q8 attention matrix closely matched its local BF16 training counterpart: cosine similarity 0.9999834 and relative L2 difference 0.5761%, consistent with a small quantization difference. This is a one-tensor check, not proof of whole-checkpoint provenance. The GGUF's original source is not recorded in its header; the runtime appendix documents its existing local compatibility adaptation.

Reproducible script: [live_lora_probe.py](../../comfy-identity/diagnostics/live_lora_probe.py). Run with the existing training Python environment, which contains NumPy, safetensors, and requests. Coordinate an idle cloud worker before running; the script deliberately does not manage another process's lifecycle.

```powershell
& comfy-identity/training/.venv/Scripts/python.exe comfy-identity/diagnostics/live_lora_probe.py --profile PROFILE_ID --name UNIQUE_EXPERIMENT_NAME
```

Exact graphs, histories, raw latent arrays, and metrics remain private in ignored `comfy-identity/training-comparisons/jon-live-lora-causal-probe/`. The worker was resumed after the test and its readiness checks passed. No checkpoint was retrained or overwritten.

## Training is producing real updates

The CPU audit found 128 finite, nonzero, shape-matched attention updates, represented by 256 A/B tensors. All recorded health checkpoints had nonzero B gradients and changed B parameters. Rank 16 and alpha 16 give a training multiplier of 1. The optimizer, gradient path, cached-caption indexing, and saved-state extraction showed no obvious implementation error.

Across the targeted matrices, aggregate `||BA|| / ||W||` was 0.310%, 0.429%, and 0.609% for the controlled 20-, 40-, and 80-step adapters. The earlier 400-step adapter was 1.467%, but used different captions and a different trigger. These are parameter norms against the original BF16 checkpoint, not identity scores or norms against the quantized runtime. Full method and limits: [training audit](LORA_TRAINING_AUDIT.md).

This rules out empty saved weights and a wholly frozen training path. It does not prove that five low-resolution photos taught a robust identity. Subject training associates target photos with captions; the official paired-edit trainer instead uses condition/target images and edit instructions. Our subject adapter's usefulness inside a multi-reference edit remains an empirical question. [Official Qwen 2.1 training guide](https://github.com/huggingface/diffusers/blob/main/examples/dreambooth/README_qwenimage21.md)

## Is the realism pass hiding the effect?

The graph applies the personal adapter in the first sampler only. Refinement uses the base model plus the realism adapter at denoise 0.04. That made refinement a reasonable suspect, but the saved outputs do not show it erasing the measured difference.

Using the existing matched 1024-pixel outputs, the mean absolute RGB difference in a fixed face-region box between **no personal LoRA and the 80-step adapter** was **4.66986/255 before refinement** and **4.67099/255 after refinement**. The 20- and 40-step cases likewise retained similar differences before and after. These are simple pixel statistics, not likeness scores; the box includes surrounding pixels, and the older full-image experiment lacks a fresh zero-repeat control.

The available evidence therefore does not support refinement wiping out the entire effect. It cannot rule out subtle changes to particular identity features. Numeric records remain in ignored `.local/lora-output-differences.json`.

## What may explain the weak visual benefit

These are hypotheses, not diagnosed causes:

1. **The reference already supplies much of the visible identity.** A modest additional learned update could be redundant in this pose. Test the personal adapter without an identity image or BFS to isolate what it learned.
2. **The adapter may be weak or insufficiently identity-specific.** Twenty to eighty updates correspond to roughly four to sixteen passes over five photos. Generic captions and limited image quality can constrain what is learned. Similar reference-driven outputs do not establish convergence.
3. **The training and inference tasks differ.** Caption-conditioned subject learning is being used inside a three-reference edit with pose, expression, and garment instructions. A learned text-to-image identity may have little extra influence in that setting.
4. **Other adapters, precision, and conditioning may reduce or redirect the effect.** The live test shows an effect with BFS stacked, but does not isolate whether BFS helps or competes with personal identity. Exact GGUF/Comfy revisions and supported tensor mappings matter; old Qwen issues cannot automatically be applied to Qwen 2.1. See [dated upstream research](LORA_UPSTREAM_RESEARCH.md).

Increasing inference steps or upscale resolution cannot by itself establish which explanation is correct. Neither changes what identity information was learned during training.

## Most useful next experiment

Use a short, fixed **text-to-image personal-adapter test with no reference images, BFS, realism, or upscale**. Keep identical prompt text and seeds across adapter-off/on cases, including the trained trigger. Ask for several simple views and compare against held-out photos. This asks whether the adapter itself carries useful identity information.

- If it produces recognizable identity there but adds little to reference editing, investigate reference conditioning and adapter interaction.
- If it changes outputs without learning recognizable identity, investigate data/captions and training duration before changing the app's onboarding policy.
- If stronger weights merely distort the output, that is not evidence that the stronger setting improves likeness.

After that, compare checkpoints from one continuous training run rather than separately configured runs. The original 400-versus-80 comparison changed captions and trigger, so it cannot establish the isolated effect of training length.

For the second consultant, the key remaining question is: **why do demonstrably active personal weight updates add little apparent likeness in this particular reference-driven edit?** Ask them to distinguish a loading failure from weak subject learning and redundant/competing conditioning, and to provide an exact version, source, and discriminating test for any proposed bug.
