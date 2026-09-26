# THREAD — GT Hacks virtual try-on

**Live app: [gt-hacks-thread-2026.firebaseapp.com](https://gt-hacks-thread-2026.firebaseapp.com)** · **[Three demo QR codes](https://gt-hacks-thread-2026.firebaseapp.com/tags/demo-clothes.html)**

THREAD is a deployed virtual try-on web app backed by Firebase and a local ComfyUI GPU worker. A garment QR code opens its item in the app. After Google sign-in, onboarding is **height/weight, one selfie and consent** — no photo set and no identity training. In about a minute the worker builds the person's **personal base**: the fixed model pose with their own face, hair or head covering, beard, glasses and skin tone, and a neutral expression whatever face they pulled in the selfie. Every scan then composites the garment onto that look on the CPU in about 1.4 seconds of worker time, saving a 1024 PNG and a 2048 × 2048 JPEG. See the [personal-base guide](comfy-identity/FAST_TRYON_README.md).

Each garment is rendered once on the base model with Qwen Image 2.1 when the worker starts. The body, pose and build are the base model's, so results are an appearance preview, not a fit estimate.

Take a selfie with the camera or choose a recent one from the photo library. Measurements default to **feet, inches and pounds**, with a metric option. The app stores normalized centimetres/kilograms and the chosen display preference.

The earlier trained-adapter path (eight photos, LoRA training, 80-step diffusion + 4K upscale, several minutes per image) remains available with the worker's `--pipeline qwen` flag and its workflows below; existing trained accounts keep working on the fast path through their saved reference selfie.

The project currently works on **still images**. Firebase hosts the app, authentication, queue and private assets; inference and training run on the local PC. The base pose and body come from the catalog image, so the result is a visual preview rather than a clothing-fit measurement. Firebase Storage was selected after the user enabled billing. A separately operated Linux server remains a future option and is not connected to this deployment.

## Run the deployed demo

Open the [three-code demo page](https://gt-hacks-thread-2026.firebaseapp.com/tags/demo-clothes.html) on a PC and scan a code with a phone. Each card also opens a larger code. The [local copy](docs/tags/demo-clothes.html) and individual tags can be displayed offline or printed with their white QR borders intact; the phone needs internet for the app. A new account retains the scanned selection through onboarding. Start the worker a few minutes before presenting so every active garment is pre-rendered; after that, onboarding and each scan take seconds. Scan one piece at a time and wait for its result before starting the next.

| Tag | Local garment source | Clothing |
| --- | --- | --- |
| `thread-1` | `ClothesSwap/THREAD1.png` | Navy and white striped polo |
| `thread-2` | `ClothesSwap/THREAD2.png` | Dragon graphic T-shirt |
| `thread-3` | `ClothesSwap/THREAD3.png` | Olive hooded jacket |

All three active Firebase catalog entries use the existing white-shirt pose image as their base. Their stored garment/base images were checked against the normalized source files. `pnpm qr:demo` regenerates both the local tags and `web/public/tags/` copies; build and deploy Hosting to publish them. The earlier `demo-shirt` tag remains available.

Keep the **GPU PC, ComfyUI and local worker online**. On the configured Windows machine, start ComfyUI, then run this from the repository root:

```powershell
.\scripts\start-worker.ps1
```

The script checks readiness before starting the worker and avoids starting a second copy. Use `-CheckOnly` to check without launching it; logs are under ignored `.local/`. First-time setup and credential requirements are in the [worker guide](services/worker/README.md); the app's data flow and deployment configuration are in [APP_ARCHITECTURE.md](docs/APP_ARCHITECTURE.md).

**Validation status (personal-base update, September 26, 2026):** 72 worker tests and 107 web tests pass; the web app type-checks and builds. Rules were unchanged since the 145-test emulator run. Nine selfies went through the acceptance run with identical settings: Jon plus seven generated test people covering long curly hair, glasses, a hijab, silver hair, long hair, a full beard and locs, some grinning or laughing. On the developer's visual review, hair, coverings, beards, glasses and skin tone carried over, and expressions came out neutral. Worker timing against the real local ComfyUI/parser, with Firebase stubbed: onboarding about 53 s warm, scans 1.35–1.56 s including during another person's onboarding. Firebase and phone network time is additional and was not measured live. **The web app changes need `pnpm run deploy`**; the restarted worker keeps earlier accounts working.

Historically, the earlier onboarding flow completed eight-photo validation, five-photo selection and 400 training steps in about **6 minutes 50 seconds including queue time**. Its first generation hit an RGBA/RGB mismatch at the upscaler, which was corrected in both 4K graphs; that old account data was subsequently cleared by the requested reset. A fresh version-3 account run has since completed training in **4 minutes 51 seconds** and the full patched generation in **7 minutes 51 seconds**, with completed job/history records and saved 1024/4096 outputs. Those current results were preserved during this update. See [reset scope and instructions](docs/APP_ARCHITECTURE.md#operator-account-reset).

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
| [Garment Styled 2K](comfy-identity/Qwen21_Garment_Styled_2K.api.json) (API) | One-time render of the base model wearing a garment, 1024 + 2K. |
| [Personal Base 2K](comfy-identity/Qwen21_Personal_Base_2K.api.json) (API) | Onboarding: the person's head, hair/covering and skin on the pose, neutral expression. |
| [Face Swap Try-On 2K](comfy-identity/FaceSwap_TryOn_2K.api.json) (API) | Fallback for earlier accounts: selfie face swap onto a garment render. |
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
