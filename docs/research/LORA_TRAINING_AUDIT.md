# Identity LoRA training audit

Audit date: 2026-09-26. Scope: source inspection, existing training logs, and CPU-only examination of saved weights. No training, generation, weight changes, or application-default changes were performed for this audit.

**Finding:** the adapters contain real, increasing weight updates. The available evidence does not establish that those updates learned a useful identity, or that the inference runtime applies them correctly. Similar-looking try-ons cannot distinguish those explanations from a strong reference-image path overwhelming the additional adapter. The inference/loading audit is a separate check.

## What is actually trained

LoRA keeps a base matrix fixed and learns a low-rank update, conventionally `W' = W + (alpha/r) BA`. It changes the model's computation; it is not a stored gallery of faces. The original method trains the added matrices while freezing the pretrained weights. [LoRA paper](https://arxiv.org/abs/2106.09685)

Our checkpoint config is `QwenImage21Transformer2DModel`, with 32 single-stream transformer blocks and hidden width 4096. Its attention projections are `attn.to_q`, `attn.to_k`, `attn.to_v`, and `attn.to_out.0`. Text and image positions share this stream. These names match the local saved adapter keys and the official architecture, rather than an older Qwen dual-stream layout. [Official transformer source](https://github.com/huggingface/diffusers/blob/main/src/diffusers/models/transformers/transformer_qwenimage21.py)

The trainer targets those four projections in every block: **128 matrix updates / 256 A-and-B tensors**. Each pair is `[16,4096]` and `[4096,16]`. There are **16,777,216 trainable parameters**, about **0.236%** of the local visual transformer's 7,115,124,736 parameters. Feed-forward layers, modulation, VAE, and text encoder are not trained. Attention-only targeting is the official default; expanding target coverage is an experiment, not a missing-key repair. [Official Qwen 2.1 training guide](https://github.com/huggingface/diffusers/blob/main/examples/dreambooth/README_qwenimage21.md)

All inspected adapters have rank 16, alpha 16, no rank-stabilized LoRA, no DoRA, and no alpha/rank patterns in their saved PEFT metadata. Therefore their training multiplier is **alpha/r = 1**. They are not accidentally trained at one-quarter strength. Inference strength 0.8 would add a separate multiplier if the runtime loads them correctly.

## Source and gradient-path checks

The authored trainer's SHA-256 matches `source_metadata.json`; its saved upstream copy also matches the recorded checksum. The recorded Diffusers revision is `e0abab83b5df05de9e7abd788643c1a7c1e42e28`. The adaptation primarily stages memory use, uses an NF4 text encoder, retains CPU caches, and adds health checks. [Pinned upstream trainer](https://github.com/huggingface/diffusers/blob/e0abab83b5df05de9e7abd788643c1a7c1e42e28/examples/dreambooth/train_dreambooth_lora_qwenimage21.py)

Reviewed local evidence:

- Base freezing happens **before** `add_adapter`; the newly inserted A/B parameters are cast to FP32. The optimizer then receives all parameters with `requires_grad=True`. The recorded 16,777,216 trainable count includes both A and B, not just B. See [trainer](../../comfy-identity/training/trainer/train_identity_qwen21.py), lines 1522–1555 and 1647–1690.
- Captions are loaded from the `text` column. Caption embeddings and VAE latents are cached and retrieved by the same dataset indices, including when aspect-ratio buckets reorder batches. Cached frozen conditioning need not retain a gradient graph; the trainable transformer still does. See the same file, lines 823–883, 1430–1486, and 1810–1853.
- The forward pass predicts a flow-matching target from noisy image latents and caption embeddings. The loss is backpropagated, gradients clipped, and the optimizer stepped before gradients are cleared. The final save extracts the actual PEFT state from the trained transformer. See lines 1860–1970 and 2033–2049.
- At every recorded check, **128/128 B tensors had nonzero gradients and changed after optimization**. A tensors also differ between the independently trained 20/40/80-step adapters. Checks are sampled at steps 1, 2, and multiples of 10, not every update. See [health implementation](../../comfy-identity/training/trainer/local_health.py).

No obvious frozen-adapter, detached-transformer, missing-optimizer, caption/latent-index mismatch, or zero-save bug was found in this path. This is evidence about training mechanics, not a proof of identity quality.

## Effective weight changes, beyond nonzero B

I read all 128 A/B pairs and their matching **original BF16 base matrices** on CPU, checked shapes, and computed `||BA||_F` using the low-rank Gram identity `||BA||_F² = sum((BᵀB) * (AAᵀ))`. This avoids materializing every 4096×4096 update. Every pair was finite and had a nonzero effective update.

| Training updates | Aggregate `||ΔW|| / ||W||` over targeted matrices | Median per-matrix ratio | Recorded health checks |
|---:|---:|---:|---:|
| 20 | 0.310% | 0.297% | 4 |
| 40 | 0.429% | 0.413% | 6 |
| 80 | 0.609% | 0.580% | 10 |
| Earlier 400 | 1.467% | 1.418% | 42 |

These are strength-1 norms, not prediction changes or identity scores. The denominator is the unquantized checkpoint, not the NF4 training realization or the Comfy GGUF realization. Small relative parameter updates can still change outputs substantially; there is no validated percentage threshold for sufficient likeness. The 400-step adapter used different captions, so its row is not a controlled step-only comparison.

All four files contain 256 FP32 tensors and are 67,143,792 bytes. Equal file size is expected because rank and target coverage are unchanged; it does not imply equal weights. Aggregate evidence remains locally in `.local/lora-training-weight-audit.json`; no private photos, captions, weights, or account data are included here.

## What the current experiment does not prove

**Twenty updates are only about four passes over five photos**, with batch size and accumulation both 1. Forty and eighty updates correspond to about eight and sixteen passes. The official Qwen example uses 500 optimizer updates with accumulation 4; that is context, not a recommendation to copy its schedule or a guarantee for faces. Learning rate 0.0001 matches the example, and rank 16 is not unusually small relative to that example's rank 4. Undertraining at 20–80 steps remains plausible; “the pictures barely changed” is not evidence that training has converged. [Training recipe](https://github.com/huggingface/diffusers/blob/main/examples/dreambooth/README_qwenimage21.md)

Our task is **caption-conditioned subject training**, not paired face-swap training. The five photos teach a trigger-associated appearance; they do not directly teach preserving a separate base body's expression or transferring a garment. The official editing trainer uses condition/target image pairs and an edit instruction, whereas this trainer has only the target image. Using a subject LoRA inside an editing pipeline is therefore a transfer test. [Paired editing trainer](https://github.com/huggingface/diffusers/blob/main/examples/dreambooth/train_dreambooth_lora_qwenimage21_img2img.py)

The 20/40/80 trials use identical photos, captions, trigger, preprocessing, and scalar settings. They are separate runs, not checkpoints from one continuous trajectory: small shared-step loss differences remain, so bitwise determinism is not established. The earlier 400-step run used detailed per-photo captions and a different trigger. Generic captions may entangle identity with background, clothing, expression, and historical grooming; that is an untested limitation, not an identified implementation failure.

Training loss is a noisy reconstruction objective across randomly sampled images, noise, and timesteps. A final loss value is not a facial-likeness metric. Neither rank, alpha, learning rate, target coverage, nor step count has been optimized against a held-out identity evaluation here.

## Recommended decisive checks

1. Verify the actual runtime applies all 128 intended updates with the expected scale, including its GGUF path, and measure a denoiser-output difference on exactly the same input tensors. A successful file read or UI status alone is insufficient.
2. Run a personal-LoRA on/off comparison with the **same prompt, same trigger text, same seed, and same reference**. Disable BFS and the realism pass for a separate isolation test. This prevents prompt changes or other adapters from explaining the difference.
3. Test the trigger with **no identity reference image and no BFS**, using multiple fixed seeds and held-out views. This tests whether the adapter carries identity information rather than merely modifying an identity already supplied by the reference image.
4. Only after those pass, compare training checkpoints from a **single run** at several step counts, first-pass crops and final outputs separately, judged against held-out real photos. Tune captions/data, steps, or target coverage from that evidence; do not increase learning rate or rank solely because small-step try-ons look similar.

These tests were not run as part of this CPU training audit. No claim of undertraining, optimality, successful runtime application, or improved likeness should be made from weight norms alone.
