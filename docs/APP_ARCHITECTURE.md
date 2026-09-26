# THREAD app architecture

The implemented app is deployed at **[https://gt-hacks-thread-2026.firebaseapp.com](https://gt-hacks-thread-2026.firebaseapp.com)**. The [printable demo tag](tags/demo-shirt.html) opens `https://gt-hacks-thread-2026.firebaseapp.com/g/demo-shirt`. The app queues a try-on for an existing ready account; a new account keeps that item pending through Google sign-in, onboarding and training. Onboarding requires eight uploaded photos plus a separate selfie captured with the camera in that session. The selfie step has no file picker. A signed-in scanner homepage supports the phone camera, a QR image upload, and manual item-code entry. Camera access begins from a tap and requires HTTPS on a real phone.

Measurements default to US units: feet/inches for height and pounds for weight. Users can choose metric. The app stores `heightCm`, `weightKg` and the optional `measurementSystem` preference (`us` or `metric`); these remain profile data rather than physical-fit controls.

## State and storage

| Firebase location | Purpose | Client access |
| --- | --- | --- |
| Authentication | Google account UID and session | Its own sign-in |
| `users/{uid}` | Profile, height in cm, weight in kg, display-unit preference, training consent, server-owned identity status | Owner reads; limited profile fields writable |
| `users/{uid}/queue/current` | Atomic pointer to one active request | Owner may replace only after the previous job is terminal |
| `jobs/{jobId}` | Durable train/generate request, progress, worker lease, error | Owner creates a constrained queued request and reads; worker updates |
| `garments/{id}` | Admin-managed name, active flag, reference image and pose base | Signed-in read of active items; admin writes |
| `users/{uid}/generations/{jobId}` | Saved result/history metadata | Owner reads; worker writes |
| Storage `users/{uid}/uploads/{uploadId}/0.jpg` … `7.jpg` | Eight normalized photos | Owner creates once and reads |
| Storage `users/{uid}/uploads/{uploadId}/selfie.jpg` | Separate camera-captured identity reference | Owner creates once and reads |
| Storage `users/{uid}/identity/{version}/…` | Trained LoRA, reference, restoration manifest | Owner reads; worker writes |
| Storage `users/{uid}/generations/{jobId}/…` | Original and 4K result PNGs | Owner reads; worker writes |
| Storage `garments/{id}/…` | Product and fixed-pose images | Signed-in reads; admin writes |

Images are displayed using authenticated Storage blob reads, not permanent public download links. The bucket needs CORS for the app's exact HTTPS origins and localhost during development. The frontend has only Firebase's public web configuration. Administrative credentials stay in the GPU worker's environment and are excluded from Git.

Firebase Storage is the deployed asset store, chosen after the user enabled billing. Private uploaded photos, selfies, trained personal LoRAs and generated results are excluded from Git; the GPU PC also keeps local working profiles and model files. An independently operated Linux server or storage service is a future deployment option and is not connected to this app.

New train and generation requests use `requestVersion: 2`; new version-1 requests are rejected by Firestore rules. A training request must include all eight numbered paths, a `selfiePath` in the same owner/UUID upload directory, and a timestamp `selfieCapturedAt` no more than one hour before submission or two minutes ahead of server time. The worker checks the same window relative to the job's creation time so queue delay does not invalidate an accepted capture. All nine uploads must be nonempty JPEGs no larger than 20 MiB and cannot be overwritten by the client. Existing version-1 jobs remain owner-readable and retain their queue-lock behavior.

## GPU jobs

The website writes requests to Firestore. The local Python worker makes outbound Firebase requests and consumes one job at a time. It talks to ComfyUI only on loopback. The judge's phone never connects directly to ComfyUI, and no tunnel or open GPU port is needed.

Training validates eight uploaded photos, ranks face clarity, lighting, resolution and duplicates, selects five usable distinct pictures, and runs the existing 400-step trainer. The ninth, camera-captured selfie is screened separately for one sufficiently clear face and is excluded from those five training photos. Missing or unsuitable selfies fail with retake guidance; there is no training-photo fallback. If fewer than five uploaded photos qualify, onboarding asks for replacements. The quality heuristic cannot establish that all photos show the same person and is not a learned pose or fairness model. Camera capture, timestamps and face-quality screening are not a liveness or identity attestation. See [selection details](../services/worker/PHOTO_SELECTION.md).

Every new identity uses a schema-2 manifest. Its private `reference.png` is the normalized selfie, with source path, capture time, owner/version and SHA-256 recorded alongside the adapter. The local profile binds that image separately at `cloud-reference/reference.png`. Cached and restored profiles verify ownership and the expected reference hash; the Comfy node always resolves this dedicated image and fails if it is missing or changed. Restoring a profile preserves the saved selfie and training trigger without selecting the restore image for training. Legacy schema-1 identities retain their old selected-photo reference behavior; new profiles use the selfie exclusively as the identity image reference.

The generation job uses the account's ready trained identity, the catalog's fixed base image and selected garment, and the reviewed two-pass 4K ComfyUI graph. It stores both outputs and history. Measurements are recorded for future sizing work; the current generator does not change the fixed body's dimensions or measure clothing fit.

Job leases, a per-user request lock and a local OS worker lock prevent ordinary duplicate submissions and parallel GPU work. Reloading the page does not cancel the job. Expired in-progress jobs fail when a worker returns; they are not silently restarted. See [worker recovery details](../services/worker/README.md).

## Deployment

The updated Firebase frontend and rules are deployed, alongside the existing indexes and demo garment catalog. The installed ComfyUI profile backend/node files have been updated, restarted and confirmed to report selfie-reference support through the API. The local worker has also been restarted; readiness passed for 33 workflow nodes and eight models. Keep it running while accepting jobs; the website does not start the local PC or ComfyUI. The following steps describe setup or redeployment; billing has already been enabled for this project.

1. `pnpm install` and copy `.env.example` to `.env.local` with the project's public web configuration.
2. Enable Google in Firebase Authentication. Use `gt-hacks-thread-2026.firebaseapp.com` as the canonical app/auth domain and authorize it plus `localhost` for development. The user has verified live Google sign-in on this deployment.
3. Create the default Firestore database and Storage bucket. This deployment uses the billing-enabled Firebase project and its private Storage bucket.
4. `pnpm build`, then `pnpm exec firebase deploy --only hosting,firestore:rules,firestore:indexes,storage`.
5. Configure bucket CORS and register the garment's reference and base images using the [worker/catalog guide](../services/worker/README.md). On the configured Windows PC, start ComfyUI and run `.\scripts\start-worker.ps1` from the repository root. The script checks readiness before starting a single background worker; `-CheckOnly` runs just the check. Worker logs are under ignored `.local/`, and its credential file stays outside Git.
6. Create a physical tag: `pnpm qr https://gt-hacks-thread-2026.firebaseapp.com demo-shirt "The photographic tee"`. Print [docs/tags/demo-shirt.html](tags/demo-shirt.html) at actual size, preserving the white QR border.

The public site can be hosted while the PC performs generation. Keep the PC, ComfyUI and worker running during the demo. First-time training and rendering take minutes on the current GPU; a scan starts the job immediately but does not produce an instant finished image. Pre-onboarding a judge/demo account avoids the initial training wait.

## Operator account reset

The requested account reset has been completed: **3 cloud jobs, 3 child documents and 15 private storage objects** were cleared, its app profile was reset, and **1 associated local profile and 2 local output directories** were removed separately. A follow-up check found no remaining cloud jobs, child documents or objects for that account, and no saved identity or measurements. The Google Authentication account and basic sign-in profile were retained. The earlier training/render results described below are historical and are no longer present in that reset account's app history.

The operator-only [reset script](../scripts/reset-site-account.cjs) uses the signed-in Firebase CLI account. Its default mode previews the exact account scope without deleting anything:

```powershell
node scripts/reset-site-account.cjs YOUR_FIREBASE_PROJECT ACCOUNT_EMAIL
```

Before execution, stop the local worker, let all jobs for that account become terminal and stop submitting requests from its browser. The script refuses execution while that account has queued/running jobs or the recorded local worker process is alive. Review the preview, then explicitly execute:

```powershell
node scripts/reset-site-account.cjs YOUR_FIREBASE_PROJECT ACCOUNT_EMAIL --execute
```

This clears that account's cloud uploads, identity assets, outputs, jobs and profile child documents, and resets its app profile for onboarding. It keeps the Google sign-in account and the garment catalog. The script does **not** remove local ComfyUI profiles, checkpoints or generated files; inspect their ownership and clear only the matching account's local caches as a separate operator step. Restart the worker when maintenance is finished.

## Validation and remaining boundary

**160 web/rules tests, 35 worker tests, 11 photo-selector tests and 15 profile-backend/node tests passed — 221 total.** These cover owner privacy, job forgery, server-owned result fields, atomic queue locks, immutable uploads, selfie freshness and ownership, unit conversion/preferences, input/QR validation, strict same-project QR aliases and authentication regressions. Worker/backend tests include the RGB upscaler-input regression and strict cached/restored selfie-reference behavior. Python tests use synthetic images and mocked cloud/ComfyUI calls.

Live Google sign-in was user-verified, and the deployed UI's US-unit defaults and selfie step were visually checked. **No real capture through the new mandatory-selfie onboarding, or new training/generation run using that flow, has been completed yet.** The account reset leaves onboarding ready for that check.

In the earlier onboarding format, a real account passed eight-photo validation and five-photo selection, completed 400 training steps, and became ready about **6 minutes 50 seconds after queuing**, including queue time. Its first generation saved a 1024 × 1024 render but failed at the upscaler's RGBA/RGB mismatch. Both 4K graphs now use `SplitImageWithAlpha`. A scoped operator recovery reused the saved render and ran only the RGB upscale; immutable 1024/4096 outputs were published and job/history were atomically completed with the original error retained for audit. No training or diffusion was repeated. That account's data was subsequently cleared by the requested reset.

**The patched full graph has not yet completed a fresh, uninterrupted run from submission through both output publications.** Earlier local ComfyUI training and rendering checks are documented in the [root README](../README.md).

`pnpm test` runs the JavaScript suite; rules cases require the configured local emulators. `pnpm test:rules` starts isolated Firestore and Storage emulators (Java 21 required) for the rules suite. The Python test commands are in the [worker guide](../services/worker/README.md).

With no web configuration, the UI offers an explicitly labeled design preview. Preview actions never create an account or pretend to train/upload. Local Firebase emulator mode must be explicitly enabled with `VITE_USE_FIREBASE_EMULATORS=true` and is blocked on remote domains.
