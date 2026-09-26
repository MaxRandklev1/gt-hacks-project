# THREAD app architecture

The implemented app is deployed at **[https://gt-hacks-thread-2026.firebaseapp.com](https://gt-hacks-thread-2026.firebaseapp.com)**. The [printable demo tag](tags/demo-shirt.html) opens `https://gt-hacks-thread-2026.firebaseapp.com/g/demo-shirt`. The app queues a try-on for an existing ready account; a new account keeps that item pending through Google sign-in, eight-photo onboarding, selection, and training. A signed-in scanner homepage supports the phone camera, a QR image upload, and manual item-code entry. Camera access begins from a tap and requires HTTPS on a real phone. Live sign-in, eight-photo onboarding, identity training and private cloud result publication have been exercised. The first generation needed an operator recovery at the upscale stage; see the validation boundary below.

## State and storage

| Firebase location | Purpose | Client access |
| --- | --- | --- |
| Authentication | Google account UID and session | Its own sign-in |
| `users/{uid}` | Profile, height in cm, weight in kg, training consent, server-owned identity status | Owner reads; limited profile fields writable |
| `users/{uid}/queue/current` | Atomic pointer to one active request | Owner may replace only after the previous job is terminal |
| `jobs/{jobId}` | Durable train/generate request, progress, worker lease, error | Owner creates a constrained queued request and reads; worker updates |
| `garments/{id}` | Admin-managed name, active flag, reference image and pose base | Signed-in read of active items; admin writes |
| `users/{uid}/generations/{jobId}` | Saved result/history metadata | Owner reads; worker writes |
| Storage `users/{uid}/uploads/{uploadId}/0.jpg` … `7.jpg` | Eight normalized photos | Owner creates once and reads |
| Storage `users/{uid}/identity/{version}/…` | Trained LoRA, reference, restoration manifest | Owner reads; worker writes |
| Storage `users/{uid}/generations/{jobId}/…` | Original and 4K result PNGs | Owner reads; worker writes |
| Storage `garments/{id}/…` | Product and fixed-pose images | Signed-in reads; admin writes |

Images are displayed using authenticated Storage blob reads, not permanent public download links. The bucket needs CORS for the app's exact HTTPS origins and localhost during development. The frontend has only Firebase's public web configuration. Administrative credentials stay in the GPU worker's environment and are excluded from Git.

Firebase Storage is the deployed asset store, chosen after the user enabled billing. Private uploaded photos, trained personal LoRAs and generated results are excluded from Git; the GPU PC also keeps local working profiles and model files. An independently operated Linux server or storage service is a future deployment option, not part of the current implementation.

## GPU jobs

The website writes requests to Firestore. The local Python worker makes outbound Firebase requests and consumes one job at a time. It talks to ComfyUI only on loopback. The judge's phone never connects directly to ComfyUI, and no tunnel or open GPU port is needed.

Training validates eight uploaded photos, ranks face clarity, lighting, resolution and duplicates, selects five usable distinct pictures, and runs the existing 400-step trainer. If fewer than five qualify, onboarding asks for replacement photos. The quality heuristic cannot establish that all photos show the same person and is not a learned pose or fairness model. See [selection details](../services/worker/PHOTO_SELECTION.md).

The generation job uses the account's ready trained identity, the catalog's fixed base image and selected garment, and the reviewed two-pass 4K ComfyUI graph. It stores both outputs and history. Measurements are recorded for future sizing work; the current generator does not change the fixed body's dimensions or measure clothing fit.

Job leases, a per-user request lock and a local OS worker lock prevent ordinary duplicate submissions and parallel GPU work. Reloading the page does not cancel the job. Expired in-progress jobs fail when a worker returns; they are not silently restarted. See [worker recovery details](../services/worker/README.md).

## Deployment

The Firebase Hosting app, Firestore rules/indexes, Storage rules and demo garment catalog are deployed. Live worker startup, connectivity and readiness checks have passed. Keep the worker running while accepting jobs; the website does not start the local PC or ComfyUI. The following steps describe setup or redeployment; billing has already been enabled for this project.

1. `pnpm install` and copy `.env.example` to `.env.local` with the project's public web configuration.
2. Enable Google in Firebase Authentication. Use `gt-hacks-thread-2026.firebaseapp.com` as the canonical app/auth domain and authorize it plus `localhost` for development. The user has verified live Google sign-in on this deployment.
3. Create the default Firestore database and Storage bucket. This deployment uses the billing-enabled Firebase project and its private Storage bucket.
4. `pnpm build`, then `pnpm exec firebase deploy --only hosting,firestore:rules,firestore:indexes,storage`.
5. Configure bucket CORS and register the garment's reference and base images using the [worker/catalog guide](../services/worker/README.md). On the configured Windows PC, start ComfyUI and run `.\scripts\start-worker.ps1` from the repository root. The script checks readiness before starting a single background worker; `-CheckOnly` runs just the check. Worker logs are under ignored `.local/`, and its credential file stays outside Git.
6. Create a physical tag: `pnpm qr https://gt-hacks-thread-2026.firebaseapp.com demo-shirt "The photographic tee"`. Print [docs/tags/demo-shirt.html](tags/demo-shirt.html) at actual size, preserving the white QR border.

The public site can be hosted while the PC performs generation. Keep the PC, ComfyUI and worker running during the demo. First-time training and rendering take minutes on the current GPU; a scan starts the job immediately but does not produce an instant finished image. Pre-onboarding a judge/demo account avoids the initial training wait.

## Validation and remaining boundary

**95 JavaScript/rules tests, 30 worker tests and 11 photo-selector tests passed.** These cover owner privacy, job forgery, server-owned result fields, atomic queue locks, immutable uploads, input/QR validation, strict same-project QR aliases and authentication regressions. Worker tests include the RGB upscaler-input regression; Python tests use synthetic images and mocked cloud/ComfyUI calls.

Live checks verified the deployment, catalog, worker connectivity/readiness and user-confirmed Google sign-in. A real account uploaded eight photos; validation and five-photo selection succeeded, and all 400 training steps completed. The identity became ready about **6 minutes 50 seconds after the request was queued**, including queue time.

The first live generation completed its 1024 × 1024 render but failed at the 4K upscaler because its input had four RGBA channels instead of three RGB channels. The full and standalone 4K graphs now use `SplitImageWithAlpha` before upscaling. Operator recovery reused the existing render, converted it to RGB and ran only the upscale stage. Both 1024 × 1024 and 4096 × 4096 PNGs were uploaded using immutable object writes to the original account/job paths; job and history were atomically marked completed, retaining the original error as audit information. No training or diffusion was repeated. This was a scoped operator recovery, not an automatic retry of failed jobs.

**The patched full graph has not yet completed a fresh, uninterrupted run from submission through both output publications.** The recovered cloud outputs verify saving and completion for that job, but do not establish that remaining boundary. Earlier local ComfyUI training and rendering checks are documented in the [root README](../README.md).

`pnpm test` runs the JavaScript suite; rules cases require the configured local emulators. `pnpm test:rules` starts isolated Firestore and Storage emulators (Java 21 required) for the rules suite. The Python test commands are in the [worker guide](../services/worker/README.md).

With no web configuration, the UI offers an explicitly labeled design preview. Preview actions never create an account or pretend to train/upload. Local Firebase emulator mode must be explicitly enabled with `VITE_USE_FIREBASE_EMULATORS=true` and is blocked on remote domains.
