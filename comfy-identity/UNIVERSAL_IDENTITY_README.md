# Universal identity workflow

This local ComfyUI workflow edits a base image using a saved person's photo profile and, optionally, that profile's trained identity LoRA. Upload and train each person once, then reuse the same workflow with different base images. The shared instructions are person neutral; each profile supplies its own reference image, identity token, and trained adapter.

## Setup

- Import `Qwen21_Universal_Identity.json` into ComfyUI. The matching `.api.json` is for API requests, not the workflow editor.
- Install the `universal_identity` folder under this ComfyUI installation's `custom_nodes`, then restart the server and refresh the browser. Its `config.json` points to the local `training` and `training/profiles` directories. Moving this workspace requires updating those paths.
- This setup uses the existing Qwen Image 2.1 Q8 GGUF, Qwen3-VL 8B int8 text encoder, Qwen Image 2.1 VAE, and the corrected `bfs_head_v1_qwen_2.1_gguf_split.safetensors` adapter. It also requires the installed GGUF, Qwen 2.1, and image comparison nodes. Training uses the separate environment and model files already prepared under `training`; this folder alone is not a fresh-machine installer.

The saved workflow currently selects the imported **Jon** profile. Other profiles use the same nodes and instructions.

## Use

1. Load the image to edit in **BASE**. Its body, clothing, pose, expression, lighting, and scene provide the starting point.
2. In **PERSON**, choose a profile in **Saved person** or click **Upload photo folder**. Use a folder containing photos of one person. A profile name is optional; the folder name supplies the default.
3. Check the photos to include in training. Favor sharp, sufficiently large faces with useful variation in angle, expression, and lighting. Click a selected thumbnail to make it the reference for the current edit. The reference can be changed without retraining.
4. For a new person, click **Train identity** and wait for completion. The panel defaults to **400 training steps**. Training is a separate action: **Run** never starts training. A **2-step check** only verifies the training path and does not replace a saved identity adapter.
5. Choose an expression in **Reusable edit instructions & expression**: **Match base image**, **Neutral (closed mouth)**, or **Custom** with your own expression details. Neutral is selected in the saved workflow for its current base image; choose Match base image when reusing another expression.
6. Leave **Use trained identity** enabled to apply the selected profile's saved LoRA, then click **Run**. An untrained profile can still render using its reference photo. Results save under ComfyUI's output folder as `Universal_Identity/result...`.

Changing the selected training photos affects the next training run; it does not change an already trained adapter. Training and image generation share the GPU, so the backend prevents overlapping jobs. Photo storage and training stay on this PC.

## Quality controls

The saved defaults are **BFS head-swap strength 0.65**, **identity strength 0.8**, and **80 sampling steps**. Training steps and sampling steps are different controls.

BFS controls the head-transfer effect; identity strength controls the selected person's learned LoRA. Stronger settings may improve likeness or transfer while making skin smoother. Compare one strength at a time with the same base, reference, and seed. More sampling steps alone do not fix plastic skin or guarantee better texture. The encoder's **resolution 0** keeps the base image's size, subject to model rounding; increasing steps does not increase image resolution.

The prompt asks for the reference identity, base expression, and natural skin detail and variation. These are model instructions, not a guarantee of an exact face or unchanged pixels.

## Validation status — 2026-09-26

- **Passed:** 12 CPU backend tests, including a real Windows subprocess with a fake trainer, nonce registration, actual worker PID tracking, lock retention through process teardown, and preventing a 2-step check from replacing a saved adapter. These tests do not run GPU training.
- **Passed:** static workflow audit: 14 UI nodes, 18 links, and 13 API nodes; graph links, widget/API values, and custom-node input/output schemas match.
- **Passed:** real browser folder upload of five photos; changing the reference photo; excluding and restoring a training photo; local profile persistence.
- **Passed:** real 2-update GPU training launched through the panel. Both updates produced finite losses and changed all 128 LoRA B tensors; the saved adapter was verified. Peak CUDA allocation was 7.10 GiB. The diagnostic was not promoted, and the GPU lock cleared after worker exit. Full-length promotion is covered by CPU tests; the imported identity was trained for 400 real updates earlier.
- **Passed:** installed custom-node and profile endpoints, HTTP 409 for generation during training, and a successful 80-step universal workflow render in 70.6 seconds using the existing 400-step profile.

The first integration run exposed a Windows cache-path-length error. The worker now uses a short isolated cache under training/.dc; the real retry passed. The final image is Universal_Identity_result.png. It has a closed-mouth neutral expression, but the skin still has some generated smoothness; 80 steps should not be interpreted as a complete fix for plastic texture.


- **Passed:** final Saved person dropdown switches between profiles; selecting Jon and clicking Run in the browser submitted the correct 80-step, neutral-expression graph. ComfyUI successfully reused the verified render from cache. Saved workflow is left on Jon with all five photos selected.
