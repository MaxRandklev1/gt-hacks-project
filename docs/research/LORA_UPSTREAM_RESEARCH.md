# Qwen identity LoRA: upstream compatibility research

Checked **2026-09-26**. This is public-source research, not verification of the installed model, custom nodes, adapter tensors, or active graph. No models were installed, no GPU jobs were run, and no application configuration was changed for this report. Pair this with [the local runtime audit](LORA_RUNTIME_AUDIT.md).

**Finding:** upstream provides real Qwen-Image 2.1 LoRA support, but the exact architecture, tensor names, loader revision, and scaling matter. A completed image or a connected LoRA node alone does not establish that the intended adapter changed the sampled weights. Conversely, an imperfect likeness alone does not establish a disconnected adapter.

## Version boundary and dated evidence

| Primary source | Verified fact | What it establishes |
| --- | --- | --- |
| [Qwen's official repository](https://github.com/QwenLM/Qwen-Image-2.1) | Qwen-Image 2.1 released **2026-09-20**. Its visual transformer has 7B parameters and 32 single-stream layers, with Qwen3-VL 8B conditioning and a 64-channel RGBA VAE. | Treat 2.1 as a specific architecture. Original Qwen-Image/Image-Edit reports are not automatically 2.1 evidence. A local filename is insufficient provenance. |
| [ComfyUI support commit 6bfaacc](https://github.com/Comfy-Org/ComfyUI/commit/6bfaacc67c2103481e5f0c84d75257cd0581d86a), **2026-09-19** | The initial 2.1 implementation includes special LoRA mapping for fused feed-forward projections. | Check the actual installed implementation and adapter target keys before blaming training. |
| [Diffusers PR 14808](https://github.com/huggingface/diffusers/pull/14808), opened **2026-09-18**, merged **2026-09-24** | Adds separate 2.1 DreamBooth text-to-image and image-to-image LoRA trainers. A real subject example was included in review. | 2.1-specific personalization exists; training support is very recent. This does not validate our trainer or checkpoint. |
| [ComfyUI issue 9203](https://github.com/Comfy-Org/ComfyUI/issues/9203), **2025-08-06**, closed | A user reported an original-Qwen LoRA working in ModelScope but not ComfyUI. | Historical format/loader complaints exist. This predates 2.1 and is not evidence of a current unresolved 2.1 bug. |

## Concrete compatibility checks

**Tensor mapping.** In the [pinned ComfyUI LoRA mapping](https://github.com/Comfy-Org/ComfyUI/blob/6bfaacc67c2103481e5f0c84d75257cd0581d86a/comfy/lora.py#L290), the source explains: “Qwen Image 2.1 fuses gate_layer/proj at load; LoRAs address the halves”. The mapper routes `img_mlp.gate_layer` and `img_mlp.proj` adapters into offsets of `img_mlp.gate_up`. Missing this mapping could leave those targets unapplied. **This particular concern only applies if the adapter includes those layers**; an attention-only adapter does not establish that failure mode.

**Load success versus patch acceptance.** Upstream [comfy/lora.py](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/lora.py) logs unmatched input keys; [comfy/sd.py](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/sd.py) checks which loaded patches `add_patches` accepted. Record expected, mapped, accepted, and rejected counts. An adapter file loading successfully is weaker evidence than its patches reaching the model used by the sampler. Logs are useful, but different local/custom code or logging settings can hide warnings.

**GGUF is not inherently incompatible with LoRA.** The [GGUF README](https://github.com/city96/ComfyUI-GGUF) says: “LoRA loading is experimental but it should work with just the built-in LoRA loader node(s).” The [leejet implementation](https://github.com/leejet/ComfyUI-GGUF/blob/main/ops.py) carries patches on GGML tensors, dequantizes the weight, and applies `comfy.lora.calculate_weight`. That is an implemented patch path, not a guarantee about every combination of quantization and adapter.

The [author's Qwen-Image 2.1 GGUF model card](https://huggingface.co/leejet/Qwen-Image-2.1-GGUF) specifically directs ComfyUI users to **leejet's fork**. At this research snapshot, [city96's loader](https://github.com/city96/ComfyUI-GGUF/blob/main/loader.py) lists `qwen_image` but not `qwen_image21`; [leejet's loader](https://github.com/leejet/ComfyUI-GGUF/blob/main/loader.py) includes both. A wrong architecture loader usually suggests a load/support problem, not necessarily a silent LoRA-only failure. Do not replace a working installation without comparing it first.

For reproducibility, public GitHub API heads were city96 `6ea2651e7df66d7585f6ffee804b20e92fb38b8a` (2026-01-12) and leejet `373048b8403a7820620065210a691263d4da0a61` (2026-09-25). Branch links can change.

## Attached but visually weak: plausible, not proven

The [official Diffusers 2.1 training guide](https://github.com/huggingface/diffusers/blob/main/examples/dreambooth/README_qwenimage21.md) explains that alpha and rank independently control the training adapter's scale through `alpha / rank`. Increasing rank alone can weaken the effective update. This is distinct from ComfyUI's inference strength; compare actual metadata, training settings, and conversion behavior. Default targets are attention projections; feed-forward targets are optional.

That guide also describes image conditioning entering through vision-language tokens **and** clean condition latents. Its image-to-image trainer expects paired condition/target images and instructions. A subject-only DreamBooth dataset and an edit-pair dataset teach different things. The [official model description](https://huggingface.co/Qwen/Qwen-Image-2.1) supports multiple reference images and caches their conditioning prefix. **Inference:** a strong source portrait, competing instructions, or later editing passes could obscure a real identity-adapter effect. These sources do not prove that our references overpower our LoRA.

Do not infer a universal required training-step count from a documentation example. Optimizer updates, gradient accumulation, learning rate, dataset quality, and target layers all matter. Likewise, the PR's skipped tiny-dummy-model scale test is not evidence that production LoRAs are no-ops.

## Handoff: discriminate the hypotheses

1. Record base-model provenance/config, ComfyUI and GGUF commits, adapter hash/metadata/keys/rank/alpha, and the executed API graph. Confirm the sampled model descends from the intended loader and identity adapter.
2. Count matched and accepted patches, check finite/nonzero adapter tensors, and inspect representative effective weight deltas. Deserialization, attachment, nonzero weight change, and good likeness are separate claims.
3. With identical seed, prompt, sampler, resolution, and inputs, compare identity strength zero versus normal at the **first identity pass**, before realism or upscaling. Our current first pass also transfers the garment, so a separate face-only isolation test would need to hold or remove that task deliberately. Repeat if differences are near numerical noise.
4. If the adapter changes weights/output but likeness remains weak, compare reference-only, adapter-only text-to-image where supported, and their combination. Keep other LoRAs fixed initially, then isolate them. Assess likeness separately from generic pixel difference.
5. Only after these controls investigate training duration/data or adjust strength. More inference steps cannot repair an adapter that never reaches the sampled model.

For another consultant's findings, request the exact model/version, dated upstream URL, affected tensor/loader path, reproduction conditions, and a test that distinguishes their explanation from alternatives. Current evidence does **not** establish that our installed stack is broken, that increasing steps will fix identity, or that a different loader is required here.
