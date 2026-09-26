# Qwen 2.1 identity and skin workflows

## Current: Jon identity + BFS

**Qwen21_Jon_Identity** combines two Qwen Image 2.1 LoRAs: the compatible BFS head-swap adapter at strength 1.0 and `Jon_Qwen21_rank16_step400.safetensors` at strength 0.8. Keep `j0n_person` and `head_swap:` in the prompt. The first image supplies the body, pose, clothing and scene; the second supplies the identity reference. The prompt requests consistent exposed skin under the base lighting.

Use the second LoRA's strength to vary the learned identity independently of BFS. Setting it to 0 disables the personal adapter for comparison. Stronger settings can also change expression and other details; they are not automatically more accurate. The supplied comparisons use 0, 0.8 and 1.1 with the same prompt, seed 42 and 40 Euler steps.

Eight photos were provided in `HackGT/Jon`. Five usable crops were captioned and used for this pilot; the two smallest images and the low-detail hat/glasses image were excluded. Original photos are preserved. Training completed 400 updates locally on the RTX 5080 with 32 GB system RAM. The isolated training environment used NF4 base weights, rank 16 / alpha 16, batch size 1, and a learning rate of 0.0001. Setup plus training took 284.135 seconds and peak allocated CUDA memory was 7.12 GiB. All 42 scheduled gradient/weight health checks passed. These checks prove the adapter trained; they do not establish an exact likeness.

Training records and reproducible commands are under `training/`. `training/run-jon-training.ps1` refuses existing nonempty output folders. The trained adapter is about 64 MiB. No photos or trained weights were uploaded.

### BFS compatibility correction

The original BFS file contains 32 fused `gate_up` adapters, but this installed Q8 GGUF model exposes separate gate and projection weights. ComfyUI ignored those 32 modules in the earlier workflow. `bfs_head_v1_qwen_2.1_gguf_split.safetensors` fixes the mapping by copying each LoRA A matrix and splitting each B matrix into its gate/up halves, with no numeric rescaling. All unaffected tensors and split reconstruction were checked exactly. The installed mapper accepts all 224 resulting modules with matching shapes. The original adapter is preserved. No ComfyUI code or packages were changed.

`jon-comparisons-corrected/` contains the controlled comparisons with this compatible BFS copy. `jon-comparisons/` preserves the preliminary 200/400-update comparisons before that correction. The earlier images below used partially loaded BFS and should not be treated as a full-strength BFS baseline.

### Final validation

The corrected BFS-only, Jon strength 0.8 and Jon strength 1.1 renders all completed at 800×512 in roughly 35 seconds each (including API polling). The saved `Qwen21_Jon_Identity` workflow was then opened, saved and run through ComfyUI's interface successfully in 34.292 seconds. Its two model loaders were verified as BFS split 1.0 and Jon step400 0.8. `jon-ui-run-history.json` records that run. `Jon_identity_result.png` is the result and `Jon_identity_comparison.jpg` shows the reference, corrected BFS alone and the combined result.

The sampled results retain the overall body, tank top and background, while changing facial features and expression. Higher identity strength softens some detail. The user compared the reference, corrected BFS alone and the combined result, and judged the combined strength-0.8 result **more accurate**. That setting is the saved default. This one comparison does not establish an exact likeness or reliability across different images.

## Neutral expression variant

`Qwen21_Jon_Neutral` is saved in ComfyUI with the same two inputs, seed 42,
40 steps, BFS 1.0 and Jon 0.8. Its positive prompt explicitly assigns expression
to image 1: closed lips, no visible teeth, relaxed cheeks, eyelids and eyebrows.
Image 2 supplies identity. The run completed successfully and produced a
closed-mouth neutral face; `Jon_identity_neutral.png` is the result and
`jon-neutral-history.json` records the run. The smiling workflow is preserved.

## Earlier BFS-only workflow details

Installed in your running ComfyUI as **Qwen21_Identity_Skin**. Open it from Workflows.

1. **BODY / POSE / CLOTHES:** choose the base image.
2. **PERSON / FACE:** choose a clear photograph of the identity to transfer.
3. Click **Run**. Inspect the saved image and the original/result comparison.

The workflow uses your existing `qwen-image-2.1-Q8_0.gguf`, Qwen3-VL 8B INT8 encoder and Qwen 2.1 VAE. The installed adapter is [BFS Head V1 for Qwen Image 2.1](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap/blob/main/docs/qwen-image-2.1.md), `bfs_head_v1_qwen_2.1.safetensors`, 318,820,976 bytes, strength 1.0. It transfers the whole head, including hair. The prompt additionally requests consistent skin tone across every exposed skin region while preserving the base pose, clothing and lighting.

Settings: 40 Euler steps, simple scheduler, CFG 1, seed 42 fixed, no acceleration adapter. Resolution 0 keeps input dimensions rounded to multiples of 32. Large images take more memory; the verified base image is 800 × 512.

## Verified locally, September 26, 2026

- Full render completed successfully in **35.03 seconds** on your RTX 5080, using the two images already connected to your existing Qwen workflow.
- The saved workflow was opened, saved and run through ComfyUI's interface. The second run reused the render cache and successfully generated the comparison previews.
- The output visibly transfers the reference face/hair and keeps the base image's head orientation, tank top and composition, with consistent-looking exposed skin. This one test does not establish reliability for difficult angles, small faces or occlusion.
- Output: `C:\Users\maxvl\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output\Qwen21_Identity\identity_skin_00001_.png`.
- A copy of the result is `identity_skin_00001_.png` in this folder.
- The original saved workflows were preserved. Their exported backups are in this folder.

This is a generative edit, so it can change pixels outside the requested region. It does not promise an exact likeness or pixel-identical clothing/background. The skin matching is prompted behavior, not a separately trained skin-tone adapter.

`Qwen21_Identity_Skin.json` is the importable workflow. The `.api.json` version is for the local ComfyUI API. All image processing ran on this computer.

## 80-step comparison

`Qwen21_Identity_80Steps` is also saved in ComfyUI. It holds the seed, input images, prompt, adapter strength, resolution and sampler constant and increases sampling from 40 to 80 steps. Its output prefix differs so the baseline remains available. The render took 49.91 seconds with the models and conditioning already cached; this is not a cold-start speed comparison.

Result: `identity_skin_80steps_00001_.png`. The visible changes are modest and include a different smile. More steps did not produce an obvious solution to the reported likeness problem. The user knows the subject and is the best judge of identity accuracy.

A person-specific LoRA is now trained and combined with BFS in the current workflow described above. The isolated trainer adapts [official Qwen 2.1 DreamBooth training support](https://github.com/huggingface/diffusers/pull/14808). The user knows the subject and should judge whether the resulting face is actually a better likeness.
