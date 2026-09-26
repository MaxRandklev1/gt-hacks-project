# THREAD — GT Hacks virtual try-on

**Live app: [gt-hacks-thread-2026.firebaseapp.com](https://gt-hacks-thread-2026.firebaseapp.com)** · **[Printable demo shirt tag](docs/tags/demo-shirt.html)**

THREAD is a deployed virtual try-on web app backed by Firebase and a local ComfyUI/Qwen Image 2.1 GPU worker. A garment QR code opens its item in the app. The implemented onboarding flow collects Google sign-in, eight photos, measurements and training consent; the worker selects five usable photos and trains a reusable personal identity adapter. Later try-ons reuse that identity and save both the original and 4096 × 4096 result to the account's private history.

The project currently works on **still images**. Firebase hosts the app, authentication, queue and private assets; inference and training run on the local PC. The base pose and body come from the catalog image, so the result is a visual preview rather than a clothing-fit measurement. Firebase Storage was selected after the user enabled billing. A separately operated Linux storage/server deployment remains a future option.

## Run the deployed demo

Open the [app](https://gt-hacks-thread-2026.firebaseapp.com), or print [docs/tags/demo-shirt.html](docs/tags/demo-shirt.html) at actual size with its QR border intact. The tag links to the demo garment; a new account retains that selection through onboarding. Training and rendering take minutes, so prepare a demo account in advance when possible.

Keep the **GPU PC, ComfyUI and local worker online**. On the configured Windows machine, start ComfyUI, then run this from the repository root:

```powershell
.\scripts\start-worker.ps1
```

The script checks readiness before starting the worker and avoids starting a second copy. Use `-CheckOnly` to check without launching it; logs are under ignored `.local/`. First-time setup and credential requirements are in the [worker guide](services/worker/README.md); the app's data flow and deployment configuration are in [APP_ARCHITECTURE.md](docs/APP_ARCHITECTURE.md).

**Validation status:** deployment, the live garment catalog, worker startup and readiness checks are verified. **95 JavaScript/rules tests, 30 worker tests and 11 photo-selector tests passed.** The user verified live Google sign-in. A real eight-photo upload passed validation, selected five photos and completed 400 training steps, making the account's identity ready in about **6 minutes 50 seconds including queue time**.

The first live generation saved its 1024-pixel render, then failed because the upscaler received RGBA instead of RGB. Both 4K workflow graphs now explicitly remove alpha before upscaling. An operator recovery reused that saved render and ran only the RGB upscale; the 1024 × 1024 and 4096 × 4096 PNGs were saved to the original account's private cloud paths, and its job/history were atomically marked completed. Training and diffusion were not rerun. **The patched full graph has not yet been rerun from start to finish without recovery.**

## What works

- **Identity and shirt editing together:** three references supply the pose/body/scene, wearer identity, and garment design. The tested base produces a 1024 × 1024 result.
- **Reusable person profiles:** upload a folder inside ComfyUI, review/select photos, choose a reference, train an identity adapter, and reuse it in later images. Untrained profiles can also run in reference-only mode.
- **Expression controls:** match the base expression, request a neutral closed mouth, or write a custom instruction.
- **Restrained texture refinement:** a second pass refines the foreground; a local BiRefNet mask composites it over the cleaner first-pass background.
- **4× output:** a tiled Nomos DAT upscaler enlarges the finished 1024 image to 4096 × 4096 without another diffusion or face-restoration pass.

Selected results and comparisons are in [docs/demo](docs/demo/). These curated exports are included in the repository with ComfyUI's embedded workflow metadata removed. They are examples of tested results, not a benchmark across people or garments.

## Start with a workflow

Import a workflow `.json` into the ComfyUI editor. Files ending in `.api.json` are the corresponding API graphs.

| Workflow | Use |
| --- | --- |
| [Universal Try-On 4K](comfy-identity/Qwen21_Universal_TryOn_4K.json) | Complete identity + garment + refinement + 4× pipeline. |
| [Universal Try-On](comfy-identity/Qwen21_Universal_TryOn.json) | The 1024-pixel try-on pipeline, with baseline and refined outputs. |
| [Upscale Only](comfy-identity/Qwen21_TryOn_Upscale_Only.json) | Enlarge an existing final image without rerunning generation. |
| [Universal Identity](comfy-identity/Qwen21_Universal_Identity.json) | Change identity while retaining the base clothing and scene. |
| [Identity + Refinement](comfy-identity/Qwen21_Universal_Identity_TwoPass.json) | Identity workflow with foreground texture refinement and comparisons. |

After setup, choose your base and garment in their image nodes. In **PERSON**, select a saved profile or upload a photo folder. Select a reference photo, optionally train the profile, then press **Run**. Workflow exports may contain example image filenames and profile selections; replace them with your own local inputs.

Training is a separate action: **Run never starts training**. The panel defaults to 400 training steps, and its two-step diagnostic checks the training path without replacing a previously trained adapter. Training and generation share the GPU, so the extension prevents overlapping jobs.

## Local setup

This is a working prototype with reproducible source and workflow exports, not a bundled ComfyUI installer. Model weights, Python environments, and personal profiles must be prepared separately.

### ComfyUI and the profile node

1. Install a ComfyUI version with `TextEncodeQwenImage21`, `QwenImage21Cache`, native BiRefNet background removal, image comparison, and Spandrel upscaling support. Install [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) for the Q8 model loader.
2. Copy the complete [universal_identity](comfy-identity/universal_identity/) directory, including `web/`, into your ComfyUI installation's `custom_nodes/` directory. The result should be `custom_nodes/universal_identity/__init__.py`.
3. In the installed node directory, copy `config.example.json` to **`config.json`** and set absolute paths to this checkout's training directory and its profile directory:

   ```json
   {
     "training_root": "C:/Projects/gt-hacks-project/comfy-identity/training",
     "profiles_root": "C:/Projects/gt-hacks-project/comfy-identity/training/profiles"
   }
   ```

   `profiles_root` must be inside `training_root`. The real `config.json` is machine-specific and ignored by Git. Update the installed copy if you move the checkout.
4. Place the inference assets in ComfyUI's configured model directories, restart ComfyUI, and refresh its browser page. Load the chosen workflow and select the installed models in its loaders.

### Inference assets

The full workflow expects these files; none of the weights are committed:

| ComfyUI model directory | File | Source |
| --- | --- | --- |
| `diffusion_models/` | `qwen-image-2.1-Q8_0.gguf` | Pre-existing local asset; original download source was not recorded. |
| `text_encoders/` | `qwen3vl_8b_int8_convrot.safetensors` | [Comfy-Org distribution](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors) |
| `vae/` | `qwen_image_2.1_vae_bf16.safetensors` | [Comfy-Org distribution](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/vae/qwen_image_2.1_vae_bf16.safetensors) |
| `vae/` | `texture_fix_vae_for_qwen_image_2.1_bf16.safetensors` | [Texture-fix VAE](https://huggingface.co/madebyollin/texture-fix-vae-for-qwen-image-2.1) |
| `loras/` | `bfs_head_v1_qwen_2.1_gguf_split.safetensors` | Local conversion of [BFS Head V1](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap/blob/main/docs/qwen-image-2.1.md) |
| `loras/` | `elusarcas-qwen2-1-detailer-v1_gguf_split.safetensors` | Local conversion of [Detail Enhancer](https://huggingface.co/reverentelusarca/elusarcas-qwen-2.1-detail-enhancer-lora) |
| `background_removal/` | `birefnet.safetensors` | [Comfy-Org BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet) |
| `upscale_models/` | `4xNomosUniDAT_otf.safetensors` | [4xNomosUniDAT](https://huggingface.co/Phips/4xNomosUniDAT_otf) |

Personal adapters live with their local profiles and are loaded by the custom node. The two `_gguf_split` adapters are local conversions of the original BFS and Detail Enhancer files: the installed GGUF model uses separate gate/projection weights, while those adapters supply fused weights. The [conversion script](comfy-identity/convert_qwen21_lora_for_gguf.py) preserves the learned update and checks its reconstruction; use converted files for these GGUF workflows.

The Q8 model, INT8 encoder, and base VAE were already installed when this work began; their original download revisions and hashes were not captured. The encoder/VAE links above identify the distribution referenced by the original ComfyUI template, without establishing the provenance of those installed copies. Recorded revisions and verified hashes for the later refinement/masking assets are in [refinement provenance](comfy-identity/TWO_PASS_REALISM_README.md), and the upscaler's are in [4K upscaling](comfy-identity/UPSCALE_4K_README.md). See [identity setup](comfy-identity/UNIVERSAL_IDENTITY_README.md) for profile use and tested settings. Models retain their respective authors' licenses.

### Isolated identity training

The profile worker currently targets **Windows** and expects a separate `comfy-identity/training/.venv/Scripts/python.exe`. Do not install training dependencies into ComfyUI's environment.

- Prepare the isolated environment using [requirements.in](comfy-identity/training/requirements.in), the portable [verified dependency versions](comfy-identity/training/requirements.verified.txt), and the [environment notes](comfy-identity/training/ENVIRONMENT.md). The reviewed Diffusers source revision is `e0abab83b5df05de9e7abd788643c1a7c1e42e28`; place that source at `comfy-identity/training/diffusers-source` as expected by the requirements file. [Reproduction instructions](docs/REPRODUCIBILITY.md) explain the checkout and install order.
- Download the complete [Qwen Image 2.1 **Diffusers checkpoint**](https://huggingface.co/Qwen/Qwen-Image-2.1) into `comfy-identity/training/models/Qwen-Image-2.1`, including `transformer`, `text_encoder`, `vae`, `processor`, `scheduler`, and `model_index.json`. The inference GGUF and INT8 encoder files cannot substitute for this training checkpoint.
- Follow the [trainer documentation](comfy-identity/training/trainer/README.md) for environment checks and the two-update diagnostic before a longer run.

Training caches captions and latents, releases the encoder/VAE, then trains rank-16 LoRA adapters on an NF4 base model. The local trainer forces Hub/dataset offline mode and disables uploads and remote experiment reporting; model downloads are a separate setup step. The photo-folder worker assigns each person a unique trigger and person-neutral captions.

## Tested hardware and behavior

Development and validation used an **RTX 5080 with 16 GB VRAM and 32 GB system RAM** on Windows. The isolated training environment used Python 3.13.12, PyTorch 2.12.1 with CUDA 13.0, Transformers 5.17.0, and bitsandbytes 0.50.2.

- A five-photo, 400-update identity pilot completed in about **4 minutes 44 seconds**, with **7.12 GiB peak allocated CUDA memory**. This is one measured training configuration, not a general memory guarantee.
- The three-reference first pass completed at **1024 × 1024**. The full try-on/refinement workflow was also run successfully through ComfyUI's interface.
- Nomos produced the selected **4096 × 4096** upscale in about **14 seconds** in the local comparison. The complete 4K graph appends that tested stage to the existing try-on pipeline.

## Current limits

The base image fixes the body shape, pose, framing, and lower clothing. This is a visual preview, **not a body measurement or clothing-fit estimate**. Identity, expression, and exposed skin matching remain generative approximations. Complex shirt photographs, logos, and lettering can be redrawn inaccurately.

The tested refinement preset is deliberately subtle: denoise **0.04**, Detail Enhancer **0.20**. Stronger second-pass tests produced unwanted texture and were rejected. Foreground masking protects the background but also includes clothing and hair; inspect garment artwork and mask edges. The 4× upscaler estimates fine detail and cannot recover information that the 1024-pixel render never contained.

## Repository boundaries

Versioned work includes the web app, Firebase rules, local worker, workflow JSONs/API graphs, the profile-node frontend/backend, builders and verification scripts, the reviewed trainer adaptation, documentation, and selected demo exports. Private photos, personal LoRAs, local profile datasets, trained checkpoints, downloaded model weights, virtual environments, caches, logs, bulk render outputs, credentials, and machine-specific configuration are excluded from Git. App photos, personal adapters and generated results use owner-scoped private Firebase Storage paths; local training and inference also retain working files on the GPU PC.

The detailed guides record the tested path and earlier experiments: [try-on](comfy-identity/TRYON_README.md), [profiles](comfy-identity/UNIVERSAL_IDENTITY_README.md), [refinement](comfy-identity/TWO_PASS_REALISM_README.md), [upscaling](comfy-identity/UPSCALE_4K_README.md), and [training](comfy-identity/training/trainer/README.md).
