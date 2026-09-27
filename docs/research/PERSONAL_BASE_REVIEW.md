# Review of the personal-base try-on

Reviewed September 26, 2026. Subject: [`d7a6184`](https://github.com/MaxRandklev1/gt-hacks-project/commit/d7a6184), following the [face-only review](FAST_TRYON_REVIEW.md). This is a review of the implementation and saved evidence, not a claim that universal likeness or live phone latency has passed acceptance.

## Assessment

The architecture now addresses the main limitation of the previous inner-face swap: onboarding generates the person's whole head and exposed skin once, then scans place cached clothing onto that personal base. This is a meaningful improvement. The code does not train a personal LoRA or run another face swap for new personal-base accounts.

It is ready for a supervised real-account trial, not a universal-product claim. Existing examples show that longer hair and head coverings can transfer, but visible hand/arm skin mismatches and soft shoulder fringes remain. Only one template body is used. Height and weight are stored but do not alter that body or establish clothing fit.

## Implemented path

- **Onboarding:** measurements first, then a recent/captured selfie and consent. A CPU parser crops the reference; Qwen with the installed BFS adapter runs 24 steps with a neutral-expression, whole-head and exposed-skin prompt. There is no texture-refinement pass. The result is locked back to the original pose outside the head/hair/skin edit mask. The worker saves the personal base, hair/covering and tee masks privately, with account-specific paths and SHA-256 validation.
- **Per garment:** the fixed template wearing the garment is generated ahead of time and cached. Its clothing mask is computed automatically, and a pose-alignment threshold is checked.
- **Per scan:** for a new personal-base identity and an existing garment cache, CPU compositing transfers the clothing and restores hair/coverings over it. A separate worker thread allows this path to run while another onboarding job uses the GPU.
- **Compatibility:** earlier accounts still use HyperSwap from their saved selfie. They do not automatically acquire the new personal base.
- **Output:** 1024 PNG plus 2048 JPEG. The earlier 4K output is not preserved by this default path.

Implementation: [personal.py](../../services/worker/personal.py), [compose.py](../../services/worker/compose.py), [parsing.py](../../services/worker/parsing.py), [worker.py](../../services/worker/worker.py), and [workflow builder](../../comfy-identity/build_personal_base_workflow.py).

## Verification and timing

Independently passed at the reviewed commit: **72 worker tests, 107 web tests, 145 database/storage rules tests**, and the web production build. The rules tests ran in local emulators. Worker tests cover ownership, hashes, synthetic compositing and routing, but mock the expensive identity generation and do not establish likeness.

Recovered Comfy history supports approximately **49 seconds of warm generation**, with approximately **62 seconds for the first execution including the one-time pose upscale**. Parsing, masks, encoding, cloud calls and uploads add time. Saved CPU composition measurements are approximately **0.63–0.78 seconds per garment for both output resolutions**, excluding network and phone display. These are stage measurements, not an upload-to-result or phone-scan-to-result benchmark.

Some acceptance reports contain near-zero `generate` times because they reloaded saved outputs with `--reuse`. Those values must not be presented as fresh inference speed. The consultant's complete worker timings used stubbed Firebase; the live journey is still pending measurement.

This review reran both original Jon references with the final prompt, 24 steps, and all three actual demo garments. The first case took **84.14 seconds** in the acceptance harness, including initial pose upscale and garment-mask preparation; the warm second case took **57.93 seconds**. Corresponding Comfy graph times were **71.686 and 51.085 seconds**. Each garment composite at both resolutions took **0.64–0.79 seconds**. These runs exclude Firebase and phone network. Fresh timing and execution metadata remain in the ignored `review-final-jon` acceptance folder and were not overwritten with `--reuse`.

## What the image evidence establishes

The private acceptance folders contain Jon with neutral and grinning references plus seven synthetic test people. These are development examples, not a held-out real-person evaluation: prompts and compositing were adjusted after inspecting failures.

- The final prompt was rerun for the three synthetic cases labelled `a`, `c` and `g` in `run4` (long curly hair, head covering and locs).
- The saved Jon, grinning Jon and `b`, `d`, `e`, `f` examples in `run2` use an earlier prompt. They cannot establish that the final prompt works unchanged on all nine inputs.
- Fresh final-prompt runs of both Jon references completed during this review. The smiling reference **again produced invented shoulder-length hair**; the neutral reference kept shorter hair. Both mouths were closed, and both polo outputs showed a gray/white remnant under the throat. The parser crops exactly matched the earlier test crops, and execution metadata confirmed the current prompt and 24 steps. Thus the claimed hair fix did not hold on this regression test. The owner's real onboarding trial and likeness judgment remain pending.
- The agreed real-head-paste / Qwen / Qwen-plus-swap comparison was not carried out. This implementation selected Qwen personal-base generation directly.
- Some examples visibly retain different skin color on the exposed hand/arm, and some hair-to-shoulder boundaries look soft or fringed. The parser and blending heuristics improve these areas but do not guarantee consistent skin or flawless matting.
- Human recognition by the photographed people, a held-out real-person set, and full phone timing have not been established. No face-similarity score should be treated as a substitute for those judgments.

The authored [acceptance harness](../../comfy-identity/run_personal_base_acceptance.py) and [implementation walkthrough](../../comfy-identity/FAST_TRYON_README.md) describe the saved local evidence. Private reference photos, generated pictures, account identifiers and bulk outputs are intentionally not committed with this review.

## Concrete open findings

### Replacement-selfie failure and preview review — fixed in this follow-up

At `d7a6184`, a failed replacement enrollment leaves the old ready identity intact, and the onboarding selector interprets that old identity as success. The failure can therefore disappear while the user silently continues with the previous look. The original implementation also immediately proceeds after generation rather than requiring the user to accept the new likeness. Preserving the previous usable identity is reasonable; hiding the failed replacement is not.

The frontend now exposes failed replacements with an explicit **Keep my previous look** option, waits for the matching identity snapshot after job completion, and shows a larger **Does this look like you?** review before starting a pending scan. Review state persists per account in that browser; it is not synchronized across devices. Switching accounts clears the previous submitted-selfie preview. Obsolete eight-photo instructions and the unconditional under-one-minute setup claim were removed. **114 web tests**, TypeScript, and the production build pass after these changes; rules are unchanged from the 145 passing emulator checks. Relevant code: [onboarding.ts](../../web/src/lib/onboarding.ts) and [App.tsx](../../web/src/App.tsx). No likeness claim should be inferred from a preview merely being displayed.

### CPU queue isolation is conditional

`FirebaseStore.claim` applies `limit(20)` to all queued jobs before filtering by lane. An independent CPU-only probe that honored the real query limit found that twenty queued onboarding jobs ahead of a scan caused the CPU lane to claim nothing; the scan remained queued. The existing fake database's `limit()` is a no-op, so the lane test misses this condition. Filter by job kind before the query limit or page through candidates.

Also, the CPU thread claims every generation job. A legacy-account HyperSwap request or an uncached garment still submits GPU work, so that request can wait behind onboarding and block later CPU scans. “Cached personal-base scans have a separate CPU lane” is accurate; “scans never wait for GPU work” is not. See [worker.py](../../services/worker/worker.py), `claim`, `generate`, `styled_garment`, and `cpu_lane`.

### Large beard or face overlap is not protected by the hair layer

`covering_mask` preserves hair, hats and scarves, but excludes the face class so clothing can cover the neck. Consequently, a long beard or changed head shape classified as face and extending into the cached clothing area can be overwritten by the garment. A synthetic mask/pixel probe reproduced that overwrite. This is a conditional segmentation edge case, not a demonstrated defect in the current short-beard Jon image; the existing thick-beard sample ends above the collar and does not test it. See [compose.py](../../services/worker/compose.py), `covering_mask` and `compose`.

### Several agreed requirements remain incomplete

- Selfie-first onboarding and background generation while measurements are entered were not implemented; enrollment still requires measurements first.
- No base-body selection exists. Everyone receives the same template build and pose.
- Catalog items are limited to clothing compatible with the base tee's coverage. Larger uncovered areas are inpainted heuristically; general necklines, tanks and arbitrary garments have not been validated.
- Output remains 2K rather than the requested preserved 1024 + 4K pair.
- Earlier face-only accounts retain the previously reviewed swap-failure risk until they re-enroll or the compatibility path gains an explicit success check.

## Next acceptance gate

Use the owner's fresh account and a current photo to measure live onboarding, inspect and explicitly accept or reject likeness, then scan the real demo garments. Record queue, generation, upload and visible-result time separately. Do not tune the prompt to that one person and call it universal. Follow with previously unseen real people covering the appearance and clothing edge cases above; keep failures and retakes in the reported result.

## Live test preparation

The reviewed web bundle and matching Firestore/Storage rules were deployed successfully to the existing Firebase project. The requested owner account's cloud app data was reset after verifying that all jobs were idle and stopping the verified worker; Google sign-in was retained. The worker restarted with all four garment caches ready. A read-back showed no measurements, identity or jobs, and Chrome displayed the new one-selfie onboarding form with US and metric units. Local development caches were not purged by this cloud-account reset.

The next step requires the owner to complete onboarding with their own information and reference photo. Live completion, likeness approval and scan-to-visible-result timing have not yet been measured. Automated checks and local acceptance renders do not stand in for that trial.
