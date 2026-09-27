# THREAD app architecture

The implemented app is deployed at **[https://gt-hacks-thread-2026.firebaseapp.com](https://gt-hacks-thread-2026.firebaseapp.com)**. The [permanent printed tags](tags/demo-clothes.html) open the active `thread-1` through `thread-10` garment routes. The app queues a try-on for an existing ready account; a new account keeps that item pending through Google sign-in, onboarding and training. Onboarding requires height/weight and one selfie. Users can take it with the camera or choose a recent JPG, PNG or WebP. On phones and narrow screens (up to 820px), the signed-in landing screen puts the QR scanner first. A backup **Choose from the collection** button opens a separate collection screen; the catalog and its thumbnails are not loaded until the user chooses it. That screen includes **Back to scanner**, and leaving the scanner stops camera resources. Desktop keeps the heading and scanner side by side above a full-width collection with five garments per row. Choosing a garment starts the same account-scoped try-on as scanning its printed tag; a camera or typed garment code is not required. Camera access begins from a tap and requires HTTPS on a real phone.

Measurements default to US units: feet/inches for height and pounds for weight. Users can choose metric. The app stores `heightCm`, `weightKg` and the `measurementSystem` preference (`us` or `metric`). New onboarding computes BMI from those metric values to select one of five supplied body templates. This selects an approximate visual body; it does not measure physical clothing fit. See [template ranges and rollout](BODY_TEMPLATES.md).

## State and storage

| Firebase location | Purpose | Client access |
| --- | --- | --- |
| Authentication | Google account UID and session | Its own sign-in |
| `users/{uid}` | Profile, height in cm, weight in kg, display-unit preference, training consent, server-owned identity status | Owner reads; limited profile fields writable |
| `users/{uid}/queue/current` | Atomic pointer to one active train/finalize/generate request | Owner may replace only after the previous job is terminal |
| `users/{uid}/onboarding/current` | Saved current-look selfie draft bound to a training job | Owner reads; constrained writes for current onboarding, frozen during finalization |
| `jobs/{jobId}` | Durable train/finalize/generate request, progress, worker lease, error | Owner creates a constrained queued request and reads; worker updates |
| `garments/{id}` | Admin-managed name, active flag, reference image and pose base | Signed-in read of active items; admin writes |
| `users/{uid}/generations/{jobId}` | Saved result/history metadata | Owner reads; worker writes |
| Storage `users/{uid}/uploads/{uploadId}/0.jpg` … `7.jpg` | Eight normalized photos | Owner creates once and reads |
| Storage `users/{uid}/uploads/{uploadId}/selfie.jpg` | Separate camera-captured or chosen recent identity reference | Owner creates once and reads |
| Storage `users/{uid}/identity/{version}/…` | Face-swap selfie + manifest (earlier: trained LoRA, reference, restoration manifest) | Owner reads; worker writes |
| Storage `users/{uid}/generations/{jobId}/…` | 1024 PNG and 2K JPEG results (earlier: 4K PNG) | Owner reads; worker writes |
| Storage `garments/{id}/…` | Product and fixed-pose images | Signed-in reads; admin writes |
| Storage `garments/{id}/body-bases/weight-N.png` | Shared alternative body templates; template 3 can reuse `base.png` | Worker reads; operator creates |

Images are displayed using authenticated Storage blob reads, not permanent public download links. The bucket needs CORS for the app's exact HTTPS origins and localhost during development. The frontend has only Firebase's public web configuration. Administrative credentials stay in the GPU worker's environment and are excluded from Git.

Firebase Storage is the deployed asset store, chosen after the user enabled billing. Private uploaded photos, selfies, trained personal LoRAs and generated results are excluded from Git; the GPU PC also keeps local working profiles and model files. An independently operated Linux server or storage service is a future deployment option and is not connected to this app.

New requests use `requestVersion: 5`. Onboarding saves measurements, uploads one selfie to `users/{uid}/uploads/{uploadId}/selfie.jpg`, and atomically records consent while queuing an `enroll` job with the selfie path, source, selection time and (camera only) capture time. The worker checks for one clear face, crops it to head and shoulders, and generates the person's personal base on the GPU (~50 s warm). It saves the selfie, the personal base (1024, 2K, hair/covering and tee masks) and a schema-5 `mode: personal_base` manifest under `users/{uid}/identity/{jobId}/`, then marks the identity ready with `previewPath`. Once both the enrollment job and matching identity are ready, the website automatically opens the garment collection, or starts the pending scanned garment. There is no look-approval step; users can retake their selfie from their profile. Legacy browser review flags are ignored. A rejected selfie fails the job with guidance to retake it; an existing ready identity stays usable.

The identity and manifest also store a server-selected `bodyTemplate` snapshot with the template ID, unrounded BMI, source height/weight and policy version. Active garments expose a complete `bodyBaseImagePaths` map. Onboarding requires the selected variants to be prepared and share one pose hash, then generates only that one personal base. Scans use the saved identity's selection, even if the account's measurements are subsequently edited. **Update details and rebuild look** applies changed measurements through a new enrollment; existing identities without this snapshot retain their earlier fixed pose.

The previous version-4 flow below remains supported by rules and worker for in-flight clients, but the current app no longer sends it. Version-4 requests Onboarding starts with an eight-photo `train` job and training consent, without requiring measurements or a reference image yet. Measurements are saved separately while training runs. A selfie draft is saved under `users/{uid}/onboarding/current` with the training job ID, its own upload UUID/path, source, selection time, optional camera capture time, and server submission time. Selection must be within one hour before submission or two minutes ahead; camera capture must also satisfy that window and precede selection. Uploaded photos omit capture time because the original capture date is unknown. The worker validates against the saved draft submission time, so a long GPU queue does not expire an accepted reference. Client drafts are frozen once finalization is queued.

Once training is completed and measurements/reference are saved, the frontend queues a minimal `finalize` job containing the training job ID. This job attaches the reference without GPU training; it requires the matching current owner, completed training job, server-owned awaiting-reference identity and saved draft. Queue transactions deduplicate submissions and permit explicit retry after failure. Version-2/3 combined onboarding remains compatible for already-open clients and uses its original 400-step setting. All uploaded files are normalized to nonempty JPEGs no larger than 20 MiB and cannot be overwritten by the client. Existing version-1 jobs remain owner-readable and retain their queue-lock behavior.

## GPU jobs

The website writes requests to Firestore. The local Python worker makes outbound Firebase requests and consumes one job at a time. It talks to ComfyUI only on loopback. The judge's phone never connects directly to ComfyUI, and no tunnel or open GPU port is needed.

Training validates eight uploaded photos, ranks face clarity, lighting, resolution and duplicates, selects five usable distinct pictures, and runs the existing trainer for **80 steps on new version-4 requests**. The recent selfie is screened separately for one sufficiently clear face during finalization and is excluded from those five training photos. Missing or unsuitable selfies fail with guidance to take or choose another, retaining the trained adapter for retry. There is no training-photo fallback. If fewer than five uploaded photos qualify, onboarding asks for replacements. The quality heuristic cannot establish that all photos show the same person and is not a learned pose or fairness model. Camera capture, selection timestamps, user confirmation and face-quality screening are not a liveness or identity attestation. See [selection details](../services/worker/PHOTO_SELECTION.md).

Version-4 training saves the adapter and a schema-3 `training.json` manifest, then marks the identity `awaiting_reference`. This state cannot generate. Finalization produces a ready schema-2 manifest and normalized selfie under `identity/{trainingJobId}/references/{referenceUploadId}/`, while reusing the adapter at `identity/{trainingJobId}/adapter.safetensors`. The `referenceVersion` field binds these per-draft paths so a retake can recover even after a partial upload without overwriting immutable objects. The ready manifest records source kind (`live_selfie` or `recent_selfie`), selection time, owner/version, actual training steps and hashes. Only camera references have a capture time. The local profile binds the selfie separately at `cloud-reference/reference.png`. Cached/restored profiles verify ownership and hashes; missing references fail rather than falling back. Lost local adapters can be restored from the pending cloud manifest without retraining. Legacy schema-1 and fixed-path schema-2 identities remain supported.

The generation job loads the account's verified personal base (cached locally, hash-checked against its manifest) and the matching body-template garment render with its masks, then composites them on a separate CPU lane, so scans never queue behind onboarding. A missing selected-body cache fails with preparation guidance instead of starting a long render on the CPU lane. Earlier accounts without a personal base use the HyperSwap face-swap graph. It stores `result.png` (1024) and `result-2k.jpg` (2048) plus history. The worker polls every second; `--pipeline qwen` restores the earlier trained-adapter 4K diffusion graph. See [fast try-on](../comfy-identity/FAST_TRYON_README.md).

Job leases, a per-user request lock and a local OS worker lock prevent ordinary duplicate submissions and parallel GPU work. Reloading the page does not cancel the job. Expired in-progress jobs fail when a worker returns; they are not silently restarted. See [worker recovery details](../services/worker/README.md).

## Deployment

The photos-first Firebase frontend and rules are deployed, alongside the existing indexes and demo garment catalog. The installed ComfyUI profile backend/node files already support separate selfie references and did not need changes or a restart for this update. The local worker was restarted after the isolated 80-step trial; readiness passed for 33 workflow nodes and eight models. Keep it running while accepting jobs; the website does not start the local PC or ComfyUI. The following steps describe setup or redeployment; billing has already been enabled for this project.

1. `pnpm install` and copy `.env.example` to `.env.local` with the project's public web configuration.
2. Enable Google in Firebase Authentication. Use `gt-hacks-thread-2026.firebaseapp.com` as the canonical app/auth domain and authorize it plus `localhost` for development. The user has verified live Google sign-in on this deployment.
3. Create the default Firestore database and Storage bucket. This deployment uses the billing-enabled Firebase project and its private Storage bucket.
4. `pnpm build`, then `pnpm exec firebase deploy --only hosting,firestore:rules,firestore:indexes,storage`.
5. Configure bucket CORS and register the garment's reference and base images using the [worker/catalog guide](../services/worker/README.md). On the configured Windows PC, start ComfyUI and run `.\scripts\start-worker.ps1` from the repository root. The script checks readiness before starting a single background worker; `-CheckOnly` runs just the check. Worker logs are under ignored `.local/`, and its credential file stays outside Git.
6. Reproduce the permanent THREAD 1–10 tags with `pnpm qr:demo`. Print [the all-ten PDF](tags/thread-tags-print.pdf) at actual size, preserving the white QR border. For other, unreserved garments, `pnpm qr https://gt-hacks-thread-2026.firebaseapp.com garment-id "Item name"` creates an individual tag.

The [QR display](https://gt-hacks-thread-2026.firebaseapp.com/tags/demo-clothes.html) contains ten permanent tags for `thread-1` through `thread-10`, with individual larger pages and a printable PDF. Their exact HTTPS destinations, `ClothesSwap/THREADN.png` mappings and SVG SHA-256 hashes are frozen in [printed-threads.json](tags/printed-threads.json). They use the canonical `https://gt-hacks-thread-2026.firebaseapp.com/g/thread-N` routes with no expiring tokens. Preserve this hostname, these routes and the garment identities across all later app deployments and preset rebuilds; do not renumber or repurpose printed IDs. The original THREAD 1–3 SVG bytes remain unchanged.

`pnpm qr:demo` verifies every pinned SVG and existing local/deployable copy before writing the matching `docs/tags/` and `web/public/tags/` assets. It rejects a different origin or any changed SVG rather than silently overwriting it. The general `pnpm qr` command refuses these ten reserved IDs. Follow with `python scripts/create-print-tags.py` (ReportLab required) to rebuild both PDF copies from the frozen SVGs and manifest. Static tag pages contain no uploaded personal or garment photos, and the earlier `demo-shirt` tag remains archived with its garment inactive. Printed codes do not expire, but successful try-on requires internet, the hosted app, prepared garment caches and the running worker.

THREAD 1–10 are now active with their original garment assignments. THREAD 4–10 add 70 prepared male/female combinations; the ten printed garments make 10 active garments and 100 shared presets. The operator prepares them once on the worker, including pose caches and garment masks, before accepting live jobs. Onboarding creates the user's personal base, and scans reuse the matching shared garment preset; neither operation regenerates the whole catalog. Later cache invalidation or new garments require operator preparation again. This activation leaves the printed destinations, SVGs and PDF unchanged.

On September 27, 2026, the owner retired **The photographic tee** (`demo-shirt`, the print with two older people) by setting only its Firestore `active` field to `false`. It is excluded from the picker and cannot start new try-ons. Its existing assets, cached presets and user history remain stored; the ten printed garment documents and QR destinations are unchanged. Refresh an already-open picker to reload the active catalog. Do not re-seed `demo-shirt`, because `seed-garment` activates the target entry.

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

**260 web/rules tests and 54 worker tests passed for this update.** The existing 11 photo-selector and 15 profile-backend/node tests passed in the preceding validation. Coverage includes owner privacy, strict v4 requests, consent before training, questions before/after training completion, refresh recovery, frozen references during finalization, duplicate protection, failed-selfie retries without retraining, restored adapters, actual step counts, source provenance, measurement units, QR validation and account-switch authentication regressions. Python unit tests use synthetic images and mocked cloud/ComfyUI calls.

Live Google sign-in was user-verified. Desktop and 390-pixel mobile previews confirmed photos first, background progress during measurements and the separate recent-selfie step, without overflow. A new isolated local profile trained on five selected photos for **80 steps in 90.57 seconds**: 28.08 seconds setup, 52.23 seconds training, 0.16 seconds adapter saving/verification and 10.10 seconds launcher/import overhead. Peak allocated CUDA memory was 7.10 GiB; the adapter was 64.03 MiB. Original profiles were unchanged. Uploads, selection, cloud publication, finalization and queue delay are additional. **A complete real account run of split v4 onboarding and 80-step likeness evaluation remain unverified.**

In the earlier onboarding format, a real account passed eight-photo validation and five-photo selection, completed 400 training steps, and became ready about **6 minutes 50 seconds after queuing**, including queue time. Its first generation saved a 1024 × 1024 render but failed at the upscaler's RGBA/RGB mismatch. Both 4K graphs now use `SplitImageWithAlpha`. A scoped operator recovery reused the saved render and ran only the RGB upscale; immutable 1024/4096 outputs were published and job/history were atomically completed with the original error retained for audit. No training or diffusion was repeated. That account's data was subsequently cleared by the requested reset.

After that reset, a fresh version-3 account completed training in **291.38 seconds (4m51s)** and the patched full graph in **470.93 seconds (7m51s)**. Live job and history metadata confirmed completed status plus saved 1024 × 1024 and 4096 × 4096 outputs. That current account, trained identity and completed results were preserved during the photos-first update. This proves the existing full graph ran end to end; it does not validate the newly split v4 onboarding. Earlier local ComfyUI training and rendering checks are documented in the [root README](../README.md).

`pnpm test` runs the JavaScript suite; rules cases require the configured local emulators. `pnpm test:rules` starts isolated Firestore and Storage emulators (Java 21 required) for the rules suite. The Python test commands are in the [worker guide](../services/worker/README.md).

The product experience requires Google sign-in. There is no guest design-preview mode or sample generation history. If Firebase is unconfigured, sign-in is disabled with an explicit availability message. Local Firebase emulator mode must be explicitly enabled with `VITE_USE_FIREBASE_EMULATORS=true` and is blocked on remote domains.

## Garment inventory

The piece picker lists authenticated Firestore `garments` with `active == true`, orders numbered THREAD items naturally, then shows other active items. It uses the existing try-on request path and per-account queue lock. Loading, empty, retry, unavailable-photo, and in-progress states are explicit; selecting another item is disabled while a try-on is in progress. Catalog images are authenticated Storage blob reads, not public download-token URLs.

Cards prefer `thumbnailPath` and fall back to the reference `imagePath`. Prepare lightweight thumbnails with `services/worker/.venv/Scripts/python.exe scripts/prepare-catalog-thumbnails.py --help`; the script defaults to a dry run. Publishing adds content-addressed, metadata-stripped images and updates only `thumbnailPath`, leaving original references, presets, and printed destinations intact.

## Try-on result screen

After scanning or selecting a garment, the result screen shows the current outfit image followed by **Choose your next piece**, which returns to scanning. The greeting, personal-base preview and extra result cards are omitted. The image remains tappable to open the existing detail/download view. Queued and failed requests show their status or retry action instead of an older outfit. Job IDs match generation-history document IDs; the result waits for that matching image when the job and history subscriptions arrive separately.

## Welcome page video

The welcome page reserves a rectangle for the product walkthrough, shows a prominent **Sign in with Google** button, and uses the same sans-serif heading style throughout. Set the public build variable `VITE_HOMEPAGE_VIDEO_URL` to a hosted video URL when the clip is ready. The video plays muted, loops, supports inline phone playback, and exposes playback controls. Without a source (or if the video fails), the area shows a clear video placeholder. No placeholder media request is made.
