# Review of the fast try-on replacement

Reviewed September 26, 2026. Commit: `57268e2902d7bc4d838d6b231f2ca3a61484ff4a`, compared with `52045ae`. This review made no application changes, deployments, account changes, model changes, worker restarts, or new GPU submissions.

## Assessment

The speed improvement is supported by actual uncached face-swap executions. The commit replaces the default personal-LoRA workflow with **one cached Qwen render per garment, followed by a local HyperSwap face replacement per user**. It bypasses personal training and per-scan diffusion; it does not diagnose or repair the previous LoRA's limited likeness benefit.

This is a useful candidate for a deliberately limited face-preview demo. It does **not** meet the original whole-person appearance requirements: hair, head silhouette, neck/arm skin, and body remain the template's. The normal downloadable maximum also changes from 4096 to 2048 pixels. Height/weight are retained as account data, but do not personalize the fixed template body.

## What changed

| Stage | Previous default | New default |
|---|---|---|
| Onboarding | Eight photos, personal training, separate recent/captured selfie | Measurements, one recent/captured selfie, consent, face-quality check |
| Identity information | Portrait plus personal LoRA, with BFS head-swap adapter | Portrait used by HyperSwap; no personal adapter |
| Garment work | Qwen identity and garment generation per scan | Qwen garment render cached once per garment/template revision |
| Per-scan work | Diffusion, refinement, masking, upscale | Face swap independently onto cached 1024 and 2048 images |
| Current-look fidelity | Attempt to transfer the person's broader appearance | Local face region only; template hair/body remain |
| Saved outputs | 1024 PNG and 4096 PNG | 1024 PNG and 2048 JPEG |

The face-swap graph uses the installed `AdvancedSwapFaceImage` node, `hyperswap_1c_256`, SCRFD, and local mode (`api_token = -1`). Expression is taken from the cached target. The tested smiling portrait produced a closed-mouth result; one example does not establish reliable behavior for every exaggerated expression, angle, or person.

## Timing and output evidence

The saved Comfy histories for `e2egen0` and `e2egen1` show **1.167 seconds** and **0.999 seconds** of execution. Their cached-node lists were empty and `[2, 3]`, respectively: the two face-swap nodes actually executed. This supports the fast local computation claim, rather than an accidental replay of cached swap results.

The production output encodings for that example total **1,889,671 bytes**: 1,190,562-byte 1024 PNG plus 699,109-byte 2K JPEG. Upload/download time, queueing, Firestore calls, and phone display latency are additional. **A phone scan-to-visible-image time below ten seconds has not been measured.** The quoted 4–7 seconds remains an estimate. The exact 0.36-second enrollment measurement was not found in saved timing evidence and was not independently reproduced.

All four cached garments exist. The actual first preparation took 157.7, 295.9, 295.7, and 295.6 seconds, followed by swap-model warming: about **17 minutes 27 seconds total**. Unchanged disk caches should be reused on ordinary restarts. Missing/invalidated caches and new garments still cost minutes. Startup currently finishes the entire prewarm loop before accepting any jobs, including lightweight enrollment.

The Jon example visibly keeps the template's short brown hair and only partially transfers the beard. Against its exact cached target, **3.051% of the 1024 image's pixels changed**, bounded by x=395..602 and y=73..280. Every pixel below y=350 remained identical. This confirms the garment, body, neck below that region, and arms are inherited rather than personalized.

Private example: `ComfyUI-Shared/output/CloudTryOn/e2egen0/node6_00001_.png`, with `node7_00001_.png` at 2K. These images and the reference remain outside Git.

## Findings to address before making this the default demo

### P2: Failed swapping can be reported as a completed personal try-on

[`worker.py:821–835`](../../services/worker/worker.py) checks output dimensions, uploads, and marks success. The installed FaceFusion node returns the **unchanged target image** when no source/target face is detected or when several initialization/inference failures occur. Relevant installed paths are `facefusion_api/swap_local.py:62–68`, `facefusion_api/models/swapper.py:100–102,198–202`, and `facefusion_api/nodes/image_nodes.py:128–132` under `Facefusion_comfyui`.

A CPU-only reproduction executed the actual no-face branch, then supplied unchanged target outputs to the real `Worker.generate` method with mocked services. It marked the job completed and uploaded both images. Enrollment uses a different detector (OpenCV Haar), so its acceptance is not proof that SCRFD will find a usable source face. The Jon example succeeded; this finding concerns unhandled failure cases. Require explicit swap-success evidence or a wrapper that surfaces failures, rather than treating dimensions as success.

### P2: A failed replacement selfie is hidden from an existing user

[`onboarding.ts:16–18`](../../web/src/lib/onboarding.ts) derives `ready` from the preserved old identity and suppresses `enrollmentFailed` whenever ready is true. [`App.tsx:236`](../../web/src/App.tsx) exits profile editing immediately after enqueue. The worker preserves the old identity when a replacement enrollment fails. Consequently, a rejected new selfie can return the user to the fitting room using the old face without an error. Preserve the old usable identity, but show replacement failure and retry explicitly.

### P2: Existing in-progress onboarding needs migration handling

The new onboarding selector only considers `enroll` jobs. Reloading the new website during an old `train`/`finalize` job can show a fresh selfie form, upload that image, then fail to enqueue because the old job still owns the account queue. Old training requests still run 80 or 400 steps. Show existing progress or defer the new enrollment instead of directing users into an upload that cannot proceed.

### P2: The Qwen fallback is limited to accounts with trained adapters

`--pipeline qwen` changes generation routing, but enrollment still creates selfie-only identities. Those new identities lack the adapter expected by `identity_profile`. The flag can use previously trained accounts; it does not restore the old onboarding workflow for new version-5 accounts. Qualify the documentation and route or reject incompatible identity modes explicitly.

The selfie component also still refers to eight training photos and a current haircut, despite both no longer applying to the new path. Update that copy if this mode is adopted.

## Deployment state

The live bundle is still `assets/index-CM6oEx-v.js`, containing version-4/eight-photo onboarding and `image4kPath`; it lacks version-5 enrollment and `image2kPath`.

The statement that the website uses none of the new approach before deployment is inaccurate. **The running worker already uses face swapping for ready accounts' generation jobs.** Deployment is still required for the selfie-only onboarding, version-5 rules, and 2K download support. The old site can display/download new results through `imagePath` at 1024, but ignores their 2K field.

## Verification and recommendation

Independently passed: **62 worker tests, 107 web tests, 145 Firestore/Storage rules tests**, and the TypeScript/Vite production build. Rules tests used local emulators, not live writes. These tests do not establish facial likeness or end-to-end phone latency; the new worker tests mock swap output and face validation.

Keep the garment caching work. Treat the default identity-method replacement as a product decision: it is suitable only if the demo can honestly accept a localized face preview with template hair/body skin. Before deploying that choice, address the false-success and replacement-error paths, handle legacy onboarding, and measure a real phone journey. The current-look and skin-tone requirements remain unresolved by this commit.
