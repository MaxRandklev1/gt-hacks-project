# THREAD

**A personal fitting room, one scan away.**

[Open THREAD](https://gt-hacks-thread-2026.firebaseapp.com/) · [Clothing QR codes](https://gt-hacks-thread-2026.firebaseapp.com/tags/demo-clothes.html) · [Printable tags](docs/tags/thread-tags-print.pdf) · [Project story](docs/DEVPOST.md)

THREAD lets you scan a clothing tag or browse a collection and see yourself wearing the piece. Sign in with Google, enter your measurements and body style, and take or upload one current-look selfie. The system creates a reusable personal image, then combines it with prepared garment images for subsequent try-ons. Looks, reactions and wish lists are saved to your account.

The deployed app uses React, TypeScript and Firebase, with ComfyUI and a Python worker on a local GPU PC. **Try-on outputs are still images: a 1024 × 1024 PNG and a 2048 × 2048 JPEG.** The homepage also contains a product video.

This README describes the current implementation as of **September 27, 2026**. Earlier training, face-swap and 4K experiments remain in the repository and are identified below as legacy workflows.

## The current experience

1. **Sign in with Google.** There is no guest/design-preview mode. A garment scanned before sign-in stays selected through onboarding.
2. **Enter your details.** Choose male or female body style, height and weight. US measurements are the default; metric is also available. The server uses BMI to select one of five templates within the chosen style—ten templates in total.
3. **Take or choose one recent selfie.** The reference should reflect your current hair, facial hair and head covering. Confirm consent, then submit. Production does not request eight training photos, side angles or a person-specific LoRA.
4. **Set up your likeness.** Progress distinguishes catalog preparation, queueing, selfie checks, image generation, alignment and saving. Actual sampler steps are shown when available. When setup finishes, the app enters scanning automatically or starts the pending garment; there is no “Does this look like you?” approval screen.
5. **Choose a piece.** Mobile prioritizes the QR scanner, with camera access started by a tap. QR-image upload is another option. **Choose from the collection** opens a separate backup collection screen. Desktop places the scanner beside the introduction and the full collection below, with five garments per row.
6. **Keep exploring.** The result page shows the garment name and current outfit, then Like/Dislike, **Save to wish list**, and **Choose your next piece**. Tap the image for details, image download or another generation. Pending and failed requests show their own state rather than an unrelated older result.

**Your looks** expands into All looks, Liked pieces, Disliked pieces and Wish list. Like and dislike are mutually exclusive and toggleable; the wish list is independent. These preferences are saved per garment in Firebase, so they follow the account across devices. Recent history loads the latest 100 generations; saved pieces can still retrieve their referenced look outside that window.

The profile page provides **Retake your selfie** and **Update details and rebuild look**. The body selection stored with a personal image remains fixed until that image is rebuilt, keeping it aligned with the garment presets.

### Homepage and media

The homepage uses a consistent SVG brand mark on all platforms. On phones, its order is heading → product video → introduction → Google sign-in → setup steps; desktop places the video beside the copy.

The bundled [product reel](web/public/media/thread-reel.mp4) is 19 seconds, 1280 × 720 at 30 fps, silent H.264 Constrained Baseline with `yuv420p` and faststart metadata (about 2.6 MB). It plays muted, loops inline, and has native controls, a poster image and an error retry. A thin frame in the original placeholder green surrounds the rounded video. `VITE_HOMEPAGE_VIDEO_URL` can override the bundled clip. Firebase Hosting caches public `/media/**` assets for one hour; app pages retain their revalidation policy.

## How try-on works

The expensive work is reused at two levels:

| Stage | When it runs | Implementation | Result |
| --- | --- | --- | --- |
| Garment preparation | Once per garment/body-template combination, or after cache invalidation | Qwen Image 2.1 with the six-step Viggle turbo LoRA; 0.5 MP garment reference; no second diffusion pass | Shared garment render, aligned pose assets and masks |
| Personal-base creation | Once per enrollment or requested rebuild | Qwen Image 2.1 Q8, BFS head-swap LoRA at 0.65, one cropped 0.35 MP selfie, 24 sampling steps, neutral-expression instructions | Account-specific head, hair/covering and exposed-skin image on the selected pose |
| Try-on | Each scan or collection selection | CPU parsing/mask-based compositing of the prepared garment onto the personal base, with hair/coverings layered back over it | Private 1024 PNG and 2048 JPEG plus saved history |

The personal-base pass uses person-neutral instructions and pose locking. The compositor includes exposed-skin tone matching and collar/gap filling. These aim to preserve identity and blend clothing naturally; results remain approximations.

**Presets are shared on the worker, not stored separately on each visitor’s phone.** The ten active garments and ten body templates require 100 shared combinations. Onboarding creates only the user’s selected personal base, and a scan reuses the matching garment preset. An ordinary restart reuses valid disk caches. New garments, changed workflow/catalog inputs or lost caches can require preparation again.

The worker has a GPU lane for onboarding and a separate CPU lane for cached personal-base try-ons. This allows those try-ons to run while another person’s onboarding uses the GPU. Queue pressure, compatibility paths and missing preparation can still add delays; this is not an unlimited-concurrency service.

The historical CLI default is named `--pipeline faceswap`, but **new accounts use personal-base generation and CPU compositing**, not the earlier face-only swap. Production settings are patched by the worker: the personal-base JSON’s stored 80-step setting is overridden to 24 steps and its texture diffusion pass is bypassed. Default outputs are 2K, not 4K.

See the [personal-base implementation guide](comfy-identity/FAST_TRYON_README.md), [body-template policy](docs/BODY_TEMPLATES.md), and the [single-selfie production decision](docs/research/SINGLE_SELFIE_DECISION.md). The latter records the controlled comparison that retained one selfie/version A over the slower three-angle alternative.

### Timing and limits

Development used an **RTX 5080 with 16 GB VRAM and 32 GB system RAM** on Windows. Recorded measurements have different scopes:

| Measurement | Recorded time | Scope |
| --- | --- | --- |
| Turbo garment preset | About 30 seconds on tested combinations | One-time preparation; varies by garment/body and cache state |
| Warm personal-base onboarding | About 53–54 seconds | Local worker measurement with Firebase stubbed |
| Historical live enrollment | 65.83 seconds | Firestore job creation through ready write; excludes the initial photo upload, user entry time and browser download |
| Historical live garment requests | 1.71–2.47 seconds | Job creation through result storage; excludes QR acquisition and browser display |

These are recorded runs, not latency guarantees or proof of likeness. See the dated [personal-base review and live timing evidence](docs/research/PERSONAL_BASE_REVIEW.md) for what each run established, and the later [production decision](docs/research/SINGLE_SELFIE_DECISION.md) for the accepted configuration.

- The template determines pose and approximate build. BMI selects a visual template; it does not measure body proportions or predict physical clothing fit.
- Identity, hair, expression, skin matching, long beards, head coverings and garment edges can be imperfect. Broad recognition across unseen real people has not been established by a universal benchmark.
- Dense lettering, logos and photographic artwork may be distorted. Collar coverage and long hair can expose segmentation/compositing limitations.
- Automatic face-quality screening is not liveness verification or proof of identity. A chosen photo’s actual age is not verified.
- The GPU PC, ComfyUI, worker and network must be available for new results. Firebase Hosting alone does not run the image pipeline.

## Clothing collection and permanent tags

| Permanent ID | Clothing | Original local source |
| --- | --- | --- |
| `thread-1` | Navy and white striped polo | `ClothesSwap/THREAD1.png` |
| `thread-2` | Dragon graphic T-shirt | `ClothesSwap/THREAD2.png` |
| `thread-3` | Olive hooded jacket | `ClothesSwap/THREAD3.png` |
| `thread-4` | White motorsport long-sleeve shirt | `ClothesSwap/THREAD4.png` |
| `thread-5` | Black hooded puffer jacket | `ClothesSwap/THREAD5.png` |
| `thread-6` | Black number 8 football jersey | `ClothesSwap/THREAD6.png` |
| `thread-7` | Dark USA soccer jersey | `ClothesSwap/THREAD7.png` |
| `thread-8` | Black zip-up track jacket | `ClothesSwap/THREAD8.png` |
| `thread-9` | Green marathon long-sleeve shirt | `ClothesSwap/THREAD9.png` |
| `thread-10` | Black tuxedo print T-shirt | `ClothesSwap/THREAD10.png` |

The photographic tee with two older people, `demo-shirt`, is **inactive** and cannot start new try-ons. Its existing assets and historical looks are retained. Do not re-seed it unless intentionally restoring it: `seed-garment` activates its target.

The ten printed destinations are permanent:

```text
https://gt-hacks-thread-2026.firebaseapp.com/g/thread-1
… through …
https://gt-hacks-thread-2026.firebaseapp.com/g/thread-10
```

[printed-threads.json](docs/tags/printed-threads.json) fixes their exact URLs, garment assignments and SVG hashes. **Never renumber, repurpose or expire these IDs, and preserve the hostname/routes across deployments.** No tag contains an expiring token. The [online display](https://gt-hacks-thread-2026.firebaseapp.com/tags/demo-clothes.html), [local display](docs/tags/demo-clothes.html), individual tags and [print PDF](docs/tags/thread-tags-print.pdf) all use the same destinations. Print with the white QR borders intact. The legacy `demo-clothes.html` filename does not change the permanent garment routes.

`pnpm qr:demo` verifies frozen SVGs before reproducing tag pages. `pnpm qr` refuses to overwrite the reserved ten IDs. `python scripts/create-print-tags.py` rebuilds the matching PDF copies from the manifest and SVGs; it requires ReportLab. Printed tags still require internet, an available app and a running worker with the matching presets.

## Architecture and saved data

| Component | Role |
| --- | --- |
| React 19, TypeScript, Vite, ZXing | Responsive web UI, selfie capture, QR scanning and collection browsing |
| Firebase Hosting | Web app, public product reel and printed tag pages |
| Firebase Authentication | Google sign-in and account identity |
| Firestore | Profiles, catalog, durable jobs/progress, history and garment preferences |
| Private Firebase Storage | User uploads, personal-base assets, generated images and authenticated catalog images |
| Python worker + ComfyUI | GPU personal-base/preset generation, CPU human parsing and garment compositing |

The browser uploads images and queues constrained requests in Firebase. The worker makes outbound cloud calls and talks to ComfyUI over loopback; phones do not connect to the GPU directly, and no inbound tunnel is required. The separate Linux server is not part of this deployment.

- `users/{uid}`: measurements, body style, consent and worker-managed identity state.
- `jobs/{jobId}` and `users/{uid}/queue/current`: requests, progress and the per-account queue lock. Current enrollment/generation requests use version 5.
- `users/{uid}/generations/{jobId}`: private result metadata and image paths.
- `users/{uid}/piecePreferences/{garmentId}`: like/dislike/none, wish-list flag, referenced generation and update timestamp.
- Storage `users/{uid}/uploads/`, `identity/` and `generations/`: private input and output files.
- `.local/firebase-worker/`: ignored worker state and shared/personal caches on the GPU PC.

Firestore and Storage rules enforce owner access. Client code cannot assign ready identities or write generated results. Preference writes must reference that account’s completed generation of the same garment. Private images are retrieved with authenticated blob reads, not permanent public download tokens. Administrative credentials never belong in the web bundle or Git.

## Run and deploy

### Web app

Use Node.js **20.x at 20.19 or newer, or Node.js 22.12 or newer**, as required by the installed Vite version, and pnpm. From the repository root:

```powershell
pnpm install --frozen-lockfile
Copy-Item .env.example .env.local
# Fill .env.local with your Firebase project's public web configuration.
pnpm dev
```

The root environment file supplies `VITE_FIREBASE_*` values. Enable Google in Firebase Authentication, authorize the app domain and `localhost`, create Firestore and Storage, and configure bucket CORS for the exact app/development origins. Use `VITE_USE_FIREBASE_EMULATORS=true` only with the local emulator suite; emulator mode is restricted to localhost. See [Firebase setup and architecture](docs/APP_ARCHITECTURE.md) and the [worker setup guide](services/worker/README.md).

After signing in to the Firebase CLI and confirming the intended project in `.firebaserc`:

```powershell
pnpm run deploy
```

This command builds and publishes Hosting, Firestore rules/indexes and Storage rules. For a Hosting-only change, use `pnpm build` followed by `pnpm exec firebase deploy --only hosting`. Keep the existing Firebase hostname and printed tag routes. Public media in `web/public/media/` ships with the build. The current UI and media changes are already deployed; these commands are for subsequent changes or a separate installation.

### GPU worker

A checkout does not include model weights, private garment/body source images, personal photos, prepared caches or Python environments. Follow [worker setup](services/worker/README.md) and [reproducibility notes](docs/REPRODUCIBILITY.md) to prepare ComfyUI, the `universal_identity` custom node, the worker’s isolated Python environment and model files. The worker uses Firebase Application Default Credentials, `FIREBASE_PROJECT_ID` and `FIREBASE_STORAGE_BUCKET`; keep the credential outside Git and synced project folders.

The default image path needs Qwen Image 2.1 Q8, its text encoder/VAEs, the BFS GGUF-compatible head adapter, Viggle turbo adapter, BiRefNet background-removal weights, Nomos upscaler and the SegFormer ONNX human parser. The existing readiness check also inspects compatibility workflows and trainer prerequisites, even though normal onboarding does not train. See [identity setup](comfy-identity/UNIVERSAL_IDENTITY_README.md), [personal-base guide](comfy-identity/FAST_TRYON_README.md), [refinement/model provenance](comfy-identity/TWO_PASS_REALISM_README.md), and [upscaler setup](comfy-identity/UPSCALE_4K_README.md). Model files retain their authors’ licenses.

On the configured Windows PC, start ComfyUI, then run:

```powershell
.\scripts\start-worker.ps1 -CheckOnly
.\scripts\start-worker.ps1
```

The script checks readiness, avoids a second recorded worker and starts the worker in the background. Logs are in `.local/worker-stdout.log` and `.local/worker-stderr.log`. Its ignored `comfy-identity/universal_identity/config.json` must match the installed extension’s profile paths. Hosting deployment does not start this process.

Prepare missing garment/body variants before accepting users. With the live worker stopped, ComfyUI idle and the worker’s Firebase environment configured:

```powershell
services/worker/.venv/Scripts/python.exe services/worker/worker.py `
  --profiles-root comfy-identity/training/profiles --prewarm-only
```

Repeatable `--garment thread-1` and `--body-template weight-1` flags can restrict preparation. Use the same state directory as the live worker, then restart it. Normal startup also prepares missing variants before consuming jobs; valid caches are reused. `--no-prewarm` skips preparation, but does not make missing presets usable. See [catalog publication and body-template preparation](docs/BODY_TEMPLATES.md) for male/female assets, alignment checks and the recorded sleeve-repair exception that needs review after a fresh rebuild.

## Verification

Checks on September 27, 2026: **179 web tests, 146 worker tests and 174 Firestore/Storage rules tests passed**, plus TypeScript checking. Worker tests use synthetic images and mocked cloud/ComfyUI calls; these counts do not establish visual likeness or phone-to-result performance.

```powershell
pnpm test
pnpm test:rules
services/worker/.venv/Scripts/python.exe -m unittest discover -s services/worker/tests
pnpm build
```

Rules tests require Java and the Firebase Firestore/Storage emulators. Browser checks have covered mobile/desktop layout, account-saved reactions and wish lists, result routing and homepage video playback. Native iOS behavior still depends on browser autoplay and camera permissions; playback controls remain available.

## Retained workflows and research

These tools are available for local experimentation; they are not all part of web onboarding:

| Workflow / guide | Purpose |
| --- | --- |
| [Garment Styled 2K](comfy-identity/Qwen21_Garment_Styled_2K.api.json) | Current shared garment preparation |
| [Personal Base 2K](comfy-identity/Qwen21_Personal_Base_2K.api.json) | Current onboarding, with worker runtime overrides |
| [Face Swap Try-On 2K](comfy-identity/FaceSwap_TryOn_2K.api.json) | Compatibility for earlier face-only identities |
| [Universal Try-On 4K](comfy-identity/Qwen21_Universal_TryOn_4K.json) | Earlier full diffusion/identity/garment/refinement/upscale path; worker option `--pipeline qwen` |
| [Universal Try-On](comfy-identity/Qwen21_Universal_TryOn.json) | Manual 1024-pixel try-on workflow |
| [Upscale Only](comfy-identity/Qwen21_TryOn_Upscale_Only.json) | Upscale an existing image without regenerating it |
| [Universal Identity](comfy-identity/Qwen21_Universal_Identity.json) / [Two-pass Identity](comfy-identity/Qwen21_Universal_Identity_TwoPass.json) | Manual identity and refinement experiments |
| [Likeness Lab](comfy-identity/likeness_lab/README.md) | Controlled identity-method comparisons; launch with `scripts/start-likeness-lab.ps1` |
| [Training guide](comfy-identity/training/trainer/README.md) | Earlier isolated person-specific LoRA training |

Import UI `.json` workflows into ComfyUI; `.api.json` files are API graphs. Replace example image/profile selections with your own inputs. The legacy training environment is separate from ComfyUI; its 400-step defaults and eight-photo enrollment history do not describe current single-selfie onboarding. Historical research documents describe their reviewed revisions and may include behavior that has since changed.

## Repository boundaries

Versioned files include the web app, Firebase rules, worker, reusable workflows/builders, custom-node source, setup scripts, research notes, frozen tags, selected [example outputs](docs/demo/) and the explicitly published homepage video/poster. The original `ClothesSwap/` inputs remain local.

Private identity photos, personal LoRAs, profiles, training data, downloaded weights, environments, caches, bulk outputs, logs, credentials and machine configuration are ignored. Firebase stores private account images, and the GPU PC also retains local working files. The [operator account-reset procedure](docs/APP_ARCHITECTURE.md#operator-account-reset) explains the separate cloud/local scopes; resetting an account is not part of ordinary deployment.
