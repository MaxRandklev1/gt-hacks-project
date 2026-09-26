# Local Firebase → ComfyUI worker

This process polls Firebase for queued jobs and performs GPU work on the existing local ComfyUI installation. It opens no inbound port. ComfyUI stays at `http://127.0.0.1:8188`; the worker makes outbound Firebase requests with Application Default Credentials (ADC).

## Setup

1. Prepare the existing ComfyUI profile extension, isolated trainer, models and `Qwen21_Universal_TryOn_4K` workflow using the [project setup](../../README.md). Confirm ComfyUI's local `/universal-identity/status` endpoint works.
2. Create a separate worker environment and install its small dependency set. Do not add these packages to ComfyUI or the training environment:

   ```powershell
   Set-Location services/worker
   py -3.13 -m venv .venv
   & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Configure Firebase ADC on the GPU machine. A service-account credential file, if used, belongs outside Git or under the repository's ignored `.local/` directory. Never put admin credentials in the web app. Set:

   ```powershell
   $env:GOOGLE_APPLICATION_CREDENTIALS = 'C:/private/firebase-worker-service-account.json'
   $env:FIREBASE_PROJECT_ID = 'your-project-id'
   $env:FIREBASE_STORAGE_BUCKET = 'your-configured-storage-bucket'
   ```

   The deployed worker account has project-level `roles/datastore.user` and bucket-scoped `roles/storage.objectViewer` plus `roles/storage.objectCreator`. These allow Firestore job/user/history updates, object reads/listing, and creation of new outputs without object deletion or replacement. Firebase Admin bypasses client security rules, so the worker independently validates account paths and catalog references.
4. Run with the **same profiles directory** configured in the installed ComfyUI extension:

   ```powershell
   & .\.venv\Scripts\python.exe .\worker.py --profiles-root 'C:/Projects/gt-hacks-project/comfy-identity/training/profiles'
   ```

   By default the worker renders every active garment once at startup (a few minutes each, cached under `.local/firebase-worker/styled/`), builds each new account's personal base on the GPU lane (~50 s), and composites try-ons on a separate CPU lane in about 1.4 s; see [personal-base try-on](../../comfy-identity/FAST_TRYON_README.md). The human-parsing ONNX model must be in `comfy-identity/parsing-assets/`; install `onnxruntime` and `numpy` into the worker environment. `--no-prewarm` skips startup rendering and `--pipeline qwen` restores the earlier trained-adapter 4K graph. The worker polls every second.

   `--once` handles at most one available job and exits. `--workflow` can select another operator-reviewed API file; jobs cannot supply a workflow, prompt, model or filesystem path. Keep one worker per local GPU, using the default shared state directory. The worker's OS lock and private temporary files live under ignored `.local/firebase-worker`.

## Check readiness without running a job

From `services/worker`, use the same environment and ADC as the worker:

```powershell
& .\.venv\Scripts\python.exe .\worker.py --check `
  --profiles-root 'C:/Projects/gt-hacks-project/comfy-identity/training/profiles' `
  --identity-config 'C:/ComfyUI/custom_nodes/universal_identity/config.json'
```

Use the actual installed extension's config path. The check verifies that its profile directory matches, the isolated trainer's prerequisite files exist, all workflow node types and selected models are listed by ComfyUI, and local queue/training status is reachable. It reads one active catalog document at most and performs a bounded Storage object listing. Storage reads require only the worker's bucket-scoped `roles/storage.objectViewer`; normal operation also uses `roles/storage.objectCreator` for new outputs. No bucket-metadata permission is needed.

The command exits without claiming jobs, creating profiles, changing files, loading models or submitting GPU work. Output contains only setup status, counts, project/bucket IDs and actionable warnings. Omitting `--identity-config` skips the trainer/profile-config comparison and reports that limitation. A busy ComfyUI or empty catalog produces a warning; connection/config/model failures return exit code 1. A successful read check does not prove write permissions, client security rules, complete model weights or GPU execution.

## Seed the garment catalog

The catalog CLI uses `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET` and ADC, with its own named Firebase app. Explicit `--project`/`--bucket` flags go before the subcommand. Use operator-owned garment and base-pose images:

```powershell
& .\.venv\Scripts\python.exe .\catalog.py seed-garment `
  --id demo-shirt --name 'Demo shirt' --brand 'Demo' `
  --reference 'C:/private/catalog/shirt.png' --base 'C:/private/catalog/pose.png'
```

Both images are decoded, oriented, converted to RGB PNG and stripped of metadata before upload. Each input and normalized PNG is limited to 20 MiB, and input dimensions to 40 megapixels. The command creates `garments/demo-shirt/reference.png` and `garments/demo-shirt/base.png`, then merges an active `garments/demo-shirt` document. Existing images are read with a generation-pinned byte range and checked against the normalized image hash; identical bytes are reused without upload. Different bytes stop the command with instructions to use a new garment ID or an explicit admin update. Missing images use create-only generation preconditions. Other catalog entries remain intact, and no object is deleted or overwritten; object viewer plus creator permissions suffice for image seeding. Storage and Firestore are separate services, so an interrupted seed can leave only part of the update; rerun the same command to complete it. No public download token is created.

## Configure browser origins

Use an operator ADC identity with **bucket configuration permissions**, separate from the worker's object-only access:

```powershell
& .\.venv\Scripts\python.exe .\catalog.py configure-cors `
  --origin 'https://gt-hacks-thread-2026.firebaseapp.com' `
  --origin 'http://localhost:5173'
```

The Firebase hostname above is this deployment's canonical app/auth origin; substitute your own for another deployment. Repeat `--origin` for every allowed app origin. This deliberately replaces the configured bucket's CORS allowlist with only those origins and `GET`/`HEAD`, using a bucket metageneration precondition. HTTPS is accepted; HTTP is limited to exact `localhost`, `127.0.0.1` or `[::1]`. Paths, credentials, query strings, fragments and wildcards are rejected. CORS permits the browser to read responses; Firebase authentication and Storage rules still determine access. The CLI follows the official [Cloud Storage CORS configuration API](https://docs.cloud.google.com/storage/docs/samples/storage-cors-configuration).

## Job contract

The frontend creates `jobs/{jobId}` with `uid`, `kind`, `status: queued`, `requestVersion: 4` and `createdAt`. The worker checks the account's `consentVersion: identity-training-v1` and `consentAt` before any GPU work. Training begins from the photos while the user answers measurements and chooses a reference. No worker loop waits for those answers. Generation versions 1–4 and combined-training versions 2–3 remain supported; version-1 training requests must return to onboarding.

- **Train v4:** The only additional request fields are `uploadId`, a canonical UUID, and `photoPaths`, exactly `users/{uid}/uploads/{uploadId}/0.jpg` through `7.jpg` in that order. Measurements and the selfie are not required or read. The server chooses **80 updates**; callers cannot supply a step count. Training ends as a completed job with `users/{uid}.identity.status: awaiting_reference`, preserving `trainingJobId` and the trained adapter.
- **Finalize v4:** The request adds only `trainingJobId`. The worker reads `users/{uid}/onboarding/current`: `{trainingJobId, uploadId, selfiePath, selfieSource, selfieSelectedAt, submittedAt, selfieCapturedAt?}`. Its canonical `selfiePath` is `users/{uid}/uploads/{uploadId}/selfie.jpg`; this draft may use a different upload UUID from the training photos. Selection time must be within one hour before or two minutes after `submittedAt`; camera capture must be in that window and no later than selection. Chosen-file references omit capture time because choosing a file does not establish its age. The parent must be a completed v4 training job owned by this account and match its current awaiting-reference identity. Height (80–250 cm), weight (25–300 kg), unit preference and training consent must be saved. Finalization decodes and checks the selfie, binds it to the trained adapter and marks the identity ready **without retraining**. Each progress/promotion transaction rechecks the parent, identity and unchanged draft. Rules freeze the draft during a queued/running finalization. Validation failure or lease expiry preserves the trained awaiting-reference identity so the user can retry a selfie.
- **Combined training v2/v3:** Older requests continue to validate a selfie before their original 400-step training run. Version 3 declares camera/upload source and selection time; version 2 retains its camera-capture-only contract. Both use their original fixed reference paths. Timestamp freshness is relative to submission, so queue delay does not invalidate a valid request. A missing or unsuitable selfie has no reference fallback. The frontend asks the user to confirm their current hair and facial hair; neither that confirmation nor the quality heuristic proves recency, liveness or identity.
- **Training selection:** The selector reviews only the eight training uploads and chooses five distinct usable ones. The separate camera or chosen recent selfie is excluded from that dataset and remains the inference reference. Selection is a documented [quality heuristic](PHOTO_SELECTION.md), not proof that all photos show the same person. An insufficient set fails with replacement guidance.
- **Generate:** `garmentId` selects an active admin-managed catalog entry. Its two input paths must be `garments/{garmentId}/reference.png` and `garments/{garmentId}/base.png`. The wearer comes only from the requesting account's ready identity. Height/weight do not alter the fixed base body's proportions or establish physical fit.

Training creates a fresh local profile from the five chosen photos and marks it as owned by this UID/training version, with a mandatory inference reference. Before finalization, it cannot use a training photo as that reference. Progress uses the actual step count, and all eight selection records appear in `users/{uid}.identity`. Successful v4 training saves these private Storage objects:

```text
users/{uid}/identity/{trainingJobId}/adapter.safetensors
users/{uid}/identity/{trainingJobId}/training.json
```

The pending `training.json` uses schema 3 with `referencePending: true`, owner UID, training version, trigger, actual steps, selection records and adapter hash/path. It contains no inference reference. Finalization saves:

```text
users/{uid}/identity/{trainingJobId}/references/{draftUploadId}/reference.png
users/{uid}/identity/{trainingJobId}/references/{draftUploadId}/manifest.json
```

The final schema-2 manifest records `referenceVersion: draftUploadId`, the same original adapter path, actual steps, adapter/reference hashes, `referenceSource: live_selfie | recent_selfie`, `referenceSourcePath` and `selfieSelectedAt`. Only camera references have `selfieCapturedAt`; older camera manifests without selection time remain readable. Per-draft artifact paths permit a new selfie after interrupted publication without overwriting immutable objects or retraining. Retrying the same draft reuses identical bytes. Older schema-2 identities without `referenceVersion` retain their fixed `reference.png`/`manifest.json` paths.

The saved reference is always the normalized selfie. A dedicated local `cloud-reference/reference.png` and source/owner/hash metadata keep it separate from the five training photos. The Comfy identity node resolves that reference regardless of its training-photo reference-index widget; missing or changed selfie data fails instead of falling back. Restores validate provenance, including required selection time and absent capture time for uploaded references.

If a pending profile is missing, finalization restores the cloud adapter from `training.json`, retaining its original trigger and actual step count, then binds the newly validated selfie. If a ready profile or its cached reference is missing, generation restores the saved selfie and adapter. Restore-only photos are unselected so they do not become a training dataset. Another account's local profile is never adopted. Legacy schema-1 identities retain their selected-photo reference behavior and do not require a forced migration.

The v4 split-onboarding update changes the web app, rules and worker; the already-installed selfie-reference extension is compatible. Restart only the worker when idle for this update. A fresh installation or an older extension still needs the authored `comfy-identity/universal_identity/backend.py` and `__init__.py` copied into ComfyUI, followed by an idle ComfyUI restart. The schema-2 worker checks that the extension reports the matching camera or uploaded-selfie reference source before submitting generation.

Generation patches only the reviewed graph's pose/garment inputs, profile selection, and output prefixes. It keeps the existing generation, refinement, mask and upscale settings. It reads final nodes **24** (base size) and **35** (4×), strips image metadata, and writes:

```text
users/{uid}/generations/{jobId}/result.png
users/{uid}/generations/{jobId}/result-4k.png
```

History is `users/{uid}/generations/{jobId}` with owner, garment, status, paths and dimensions. The worker creates no public URLs or Firebase download tokens. The app must use authenticated Storage `getBlob`/`getBytes` and browser object URLs to display private files.

## Leases, serialization and recovery

Jobs are claimed in a Firestore transaction with a random lease token and worker ID. The 120-second lease is renewed every 20 seconds; progress, user and history writes validate owner, token, running state and expiry in the same transaction. The worker waits for ComfyUI's existing queue and training process to become idle before claiming another job. ComfyUI's profile API also rejects training while its queue is occupied.

GPU-submission POSTs are **not retried**: a lost HTTP response can still mean work was accepted. Running jobs are never adopted or requeued automatically. Expired leases become terminal failures when a worker next polls; if every worker is offline, this happens after one returns. If a worker loses its lease or is stopped, an already submitted ComfyUI job may continue locally. Let that work finish or inspect/cancel it in ComfyUI before submitting a replacement job. On restart the worker waits for local GPU work to become idle.

Successful Storage uploads use create-only generation preconditions; a repeated upload is accepted only when its recorded size/hash matches. Failed jobs can leave private partial outputs or local profiles for inspection; their new output is not promoted to a ready identity or completed generation. If retraining fails, the account's previous ready identity is restored, with `lastTrainingError` recording the failure. This also applies when a persisted lease expires after a crash. No automatic cleanup deletes personal profiles, checkpoints or cloud objects.

Keep clocks synchronized on worker machines because lease expiry uses UTC wall time. The implementation is for one operator-controlled GPU worker installation; changing `--state-dir` creates a different process lock.

## Tests and validation boundary

From the repository root, using a Python environment with the worker dependencies:

```powershell
python -m unittest discover -s services/worker/tests -v
python -m unittest discover -s tests -p test_photo_selection.py -v
```

Tests use synthetic images, temporary files and mocked cloud/Comfy calls. They cover photo-only 80-step training independent of question timing, legacy 400-step submission, progress denominators, 8→5 selection, missing-cache restoration without retraining, trigger/step preservation, reference ownership and draft-race fencing, invalid-selfie retry, partial publication followed by a new draft, graph mapping, lease fencing, private result paths, metadata removal, catalog/origin validation and read-only readiness. They do not constitute a real Firebase deployment or a live GPU training/generation test of this worker.

The Firebase adapters follow the official [Firestore transaction API](https://firebase.google.com/docs/firestore/manage-data/transactions) and [Storage generation-precondition API](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob).
