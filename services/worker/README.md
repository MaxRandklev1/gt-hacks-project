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

The frontend creates `jobs/{jobId}` with `uid`, `kind`, `status: queued`, `requestVersion: 1` and `createdAt`. The worker checks the account's `consentVersion: identity-training-v1` and `consentAt` before any GPU work.

- **Train:** `uploadId` is a canonical UUID. `photoPaths` must contain exactly `users/{uid}/uploads/{uploadId}/0.jpg` through `7.jpg`, in that order. The selector reviews all eight and chooses five distinct usable photos. Selection is a documented [quality heuristic](PHOTO_SELECTION.md), not proof that all photos show the same person. An insufficient set fails with replacement guidance.
- **Generate:** `garmentId` selects an active admin-managed catalog entry. Its two input paths must be `garments/{garmentId}/reference.png` and `garments/{garmentId}/base.png`. The wearer comes only from the requesting account's ready identity. Height/weight do not alter the fixed base body's proportions or establish physical fit.

Training creates a fresh local profile from the five chosen photos, marks it as owned by this UID/version, and starts exactly **400 updates** through the existing local profile API. Progress and all eight selection records appear in `users/{uid}.identity`. Successful training saves these private Storage objects:

```text
users/{uid}/identity/{trainingJobId}/adapter.safetensors
users/{uid}/identity/{trainingJobId}/reference.png
users/{uid}/identity/{trainingJobId}/manifest.json
```

The manifest records owner UID, version, original training trigger, selected-photo paths, local profile ID and adapter/reference hashes. If that local profile is missing on a later worker, the worker checks ownership and hashes, creates a local profile from the saved reference, imports the adapter inside that profile, and restores the original trigger. Another account's existing local profile is never adopted.

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

Tests use synthetic images, temporary files and mocked cloud/Comfy calls. They cover ownership paths, cross-account manifest rejection, missing-profile restoration, trigger preservation, 400-step submission, 8→5 selection integration, graph mapping, lease fencing, terminal states, private result paths, metadata removal, catalog/origin validation and read-only readiness. They do not constitute a real Firebase deployment or a live GPU training/generation test of this worker.

The Firebase adapters follow the official [Firestore transaction API](https://firebase.google.com/docs/firestore/manage-data/transactions) and [Storage generation-precondition API](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob).
