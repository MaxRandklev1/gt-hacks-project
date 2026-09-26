# Two-pass identity and realism workflow

Import `Qwen21_Universal_Identity_TwoPass.json` into ComfyUI. The matching `.api.json` is for API requests. `build_two_pass_workflow.py` builds this extension from the saved universal identity workflow and checks that the original API nodes and first-pass links remain unchanged.

## Use and comparison

The original **PERSON** panel still supports folder upload, photo selection, reference selection, training, and reuse of saved profiles. Use it as described in `UNIVERSAL_IDENTITY_README.md`. The person-specific LoRA and expression controls remain in the first pass; the second-pass instructions work for any selected profile.

The graph saves three outputs under ComfyUI's output directory:

| Output | What it shows |
| --- | --- |
| `Universal_Identity/result...` | Original first-pass result, unchanged. |
| `Universal_Identity_TwoPass/fixed_vae_only...` | The same first-pass latent decoded with the texture-fix VAE; no additional denoising. |
| `Universal_Identity_TwoPass/final...` | Refined foreground blended over the clean fixed-VAE baseline using an automatic soft mask. |

The lower comparison slider shows **fixed VAE only (A)** against the **masked final result (B)**. The raw second-pass image is available at node 23; the final saved image and comparison use the composite at node 29. The mask preview at node 30 helps check which areas receive refinement.

## Settings and preservation

| Stage | Current settings |
| --- | --- |
| First pass | Corrected BFS strength **0.65**, selected person's identity LoRA **0.8**, **80 steps**. |
| Second pass | Clean Qwen 2.1 model branch with only the converted Detail Enhancer at **0.20**; **40 steps**, **CFG 1**, **Euler**, **simple**, **denoise 0.04**. |
| Final composite | Local BiRefNet foreground mask from the clean baseline; refined foreground over that baseline, with no resizing or offset. |

The second sampler receives the **actual first-pass latent** directly. It does not start from the text encoder's empty output latent. The texture-fix VAE decodes that latent to supply the second pass's image reference, encodes that reference, and decodes the final result. The second model branch starts before BFS and the personal LoRA, so neither adapter is reapplied during refinement.

**Denoise 0.04 and Detail Enhancer strength 0.20 are our tested subtle preset, not the author's recipe.** The author-linked workflow instead demonstrates reference editing at denoise 1. Stronger local tests produced excessive texture and were rejected. The second-pass prompt requests restrained natural texture while preserving identity, expression, skin tone, composition, and lighting; it excludes invented blemishes, exaggerated pores, and beauty retouching. At CFG 1 these instructions belong in the positive prompt.

The subtle pass improved the tested skin but added unwanted background texture. To preserve the cleaner backdrop, node 27 loads BiRefNet and node 28 generates a soft foreground mask from the fixed-VAE baseline at node 17. Node 29 (`ImageCompositeMasked`) uses that baseline as its destination, the refined image at node 23 as its source, and the generated mask, with **x 0, y 0, resize disabled**. Node 24 saves this composite and node 25 compares it with the baseline. The universal prompt remains unchanged.

This is an automatic **foreground mask**, not a skin-only mask. It can include clothing and hair, and may need adjustment around difficult boundaries or in complex scenes. Inspect the preview; it does not guarantee perfect person segmentation in every image. Areas where the mask is zero retain the baseline exactly; soft edges blend both images.

If identity, expression, or color drifts, reduce second-pass denoise or use the first-pass/fixed-VAE-only result. Set second-pass **denoise to 0** to bypass further denoising and retain the decoder-only result. Setting **Detail Enhancer strength to 0** removes the adapter but still allows base-model denoising when denoise is above zero. At denoise **0.04**, the 40-step sampler still runs **40 steps** over the selected low-noise interval.

More steps alone do not fix plastic skin. Stronger BFS or identity settings can change both likeness and texture, and the detail pass cannot guarantee an unchanged face.

## Asset provenance

**Detail Enhancer:** [author model card](https://huggingface.co/reverentelusarca/elusarcas-qwen-2.1-detail-enhancer-lora) and [author workflow](https://huggingface.co/reverentelusarca/qwen-image-2.1-workflows/blob/main/qwen_image_2_1_upscale_v1.json). The author describes paired-data Qwen 2.1 image editing training for enhancement/restoration, with trigger **`enhance this image`**. It supports same-size edits as well as upscaling; no adapter strength is prescribed. The author notes that aggressive texture sharpening can hurt photographs.

- Source revision: `aedadc6b65a15e6b261c0ec61a4587c3e7119514`
- Original file: `elusarcas-qwen2-1-detailer-v1.safetensors`
- Original SHA-256: `c1298f51eb090473f924314475952710a2f0322f1df65c0290f3e14997244f96`
- Local converted file: `elusarcas-qwen2-1-detailer-v1_gguf_split.safetensors`
- Converted SHA-256: `282d817ef1de249dde69dde784da04c1d50271da57e6233448af270e83757297`

Conversion adapts the fused MLP keys to this GGUF model's separate gate/up projections: **32 fused modules split exactly**, duplicating each A matrix and splitting B by output rows. **320 unaffected tensors remain unchanged.** This changes storage/key layout, not the adapter's intended mathematical update. The original file is retained; the conversion report is under `realism-assets/detail-enhancer/`.

**Texture-fix VAE:** [madebyollin/texture-fix-vae-for-qwen-image-2.1](https://huggingface.co/madebyollin/texture-fix-vae-for-qwen-image-2.1), an unofficial Qwen 2.1 VAE decoder finetune targeting checkerboard texture artifacts. It can improve perceived texture while slightly reducing reconstruction accuracy; it is not lossless restoration of missing details.

- Revision: `e9f84623d22c47f8bc9fb799bc54201fa53cf80b`
- File: `texture_fix_vae_for_qwen_image_2.1_bf16.safetensors`
- SHA-256: `05e9af8da4697d1a5118b674c3d90e95f932702d2c5a8900d7d4f05076403cc7`

Source files and author cards are retained under `realism-assets/`; installed copies belong in ComfyUI's shared `models/loras` and `models/vae` directories respectively.

**Foreground masking:** [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet), used by the native `LoadBackgroundRemovalModel` and `RemoveBackground` nodes. This runs locally and supplies the soft mask for compositing.

- Revision: `35767b272f2846752a3aee1259abdd4586f735c8`
- Repository file: `background_removal/birefnet.safetensors`
- SHA-256: `9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154`
- Verified and installed as `birefnet.safetensors` in ComfyUI's shared `models/background_removal` directory.

## Validation status — 2026-09-26

- **Verified:** the decoder-only comparison was tested and visually removed the grid artifact in the tested image.
- **Verified:** the adapter conversion preserves the fused update exactly and leaves the other 320 tensors unchanged.
- **Checked by the builder:** original first-pass API/links remain intact; new links and the actual first-pass latent connection are validated.
- **Verified:** complete unmasked 80-step identity plus 40-step refinement run at detail strength 0.20 and denoise 0.04. The user approved the subtle skin result, but the backdrop was overtextured; this motivated the foreground composite. Outputs and exact submitted graph are in `realism-comparisons/subtle004*`. This is a visual check on one subject, not proof of equal quality for every identity.
- **Verified:** all 448 converted adapter tensors map to 224 actual GGUF modules with no unmatched keys or shape errors.
- **Verified:** a local BiRefNet composite using the exact saved baseline and refined images completed successfully. Visual inspection showed the refined skin with the cleaner baseline background.
- **Verified:** pressed Run in the saved ComfyUI workflow and completed all 80 identity steps, 40 refinement steps, BiRefNet masking, compositing, final saving, mask preview, and comparison. The UI's generation/compositing inputs matched the saved API export. Run ID: `60143410-b5df-4fcc-8633-14c9acdbba21`; output and history: `realism-comparisons/masked_full_ui*`.
- **Verified:** the saved-image composite changes fully background pixels by at most one 8-bit level from the baseline, and fully foreground pixels by at most one level from the user-approved refinement (soft-mask blending and PNG quantization). Hair and silhouette boundaries use the model's soft mask. Numeric report: `realism-comparisons/foreground_preservation_report.json`.
