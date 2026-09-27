# Personal-base try-on (default)

Goal: an unfamiliar person uploads one selfie and sees themselves (face, hair or head covering, beard, glasses, skin tone) in each catalog garment, in the model's pose, with a neutral expression. Nothing is prepared per person ahead of time.

New onboarding selects one of five body templates from the person's saved height and weight. The personal base and cached garment renders use the same template; the saved selection stays fixed until the person rebuilds their look. Existing accounts retain their previous pose. See [BMI ranges, catalog publication and cache preparation](../docs/BODY_TEMPLATES.md).

## How it works

1. **Once per garment and body template (catalog preparation):** `Qwen21_Garment_Styled_2K.api.json` dresses the selected base model in the garment. Output 1024 + 2K, cached under `.local/firebase-worker/styled/`. Its garment mask is computed once on the CPU.

   **Preset speed (September 27, 2026).** The graph uses the [Viggle Qwen-Image-2.1 turbo LoRA](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) with its 6-step schedule instead of 80 steps. It caps the garment reference at 0.5 MP and drops the texture pass.
   - **Time:** about **30 s per preset instead of 170–300 s**, measured on the demo tee, THREAD1 and THREAD2 on the middle body, THREAD2 and THREAD3 on the heaviest body, and THREAD1 on the slimmest.
   - **Look:** side-by-side boards against the previous renders showed the same look, including the dragon print, polo stripes and logo, and the printed photo on the demo tee.
   - **Alignment:** 0.93–0.99 against the worker's 0.85 bar.
   - **Batch estimate:** 100 presets (10 garments × 10 bodies) take about 50 minutes instead of about 10 hours.
   - **Caveat:** the turbo model's known weak spot is small dense text. Check any garment with fine lettering.
   - **Licence:** Qwen research.
   - Changing the graph changes the cache key, so existing presets re-render once.
2. **Once per person (onboarding, GPU, ~50 s warm):**
   - The selfie is parsed and cropped to head and shoulders. With no usable face, the user is asked to retake it.
   - `Qwen21_Personal_Base_2K.api.json` (BFS head-swap LoRA, selfie as reference, person-neutral prompt, neutral closed-mouth expression, 24 steps, texture pass skipped) regenerates the whole head and exposed skin on the pose base.
   - Everything outside the head/hair/skin edit region is locked back to the pose base, so the garment renders line up exactly. The worker checks lower-body alignment.
   - The resulting **personal base** plus its hair/covering and tee masks are saved privately (`users/{uid}/identity/{jobId}/personal-*`). The app shows it as "This is you in the fitting room", with a retake option.
3. **Per scan (CPU only, ~1.4 s of worker time):** the garment region of the render is composited onto the personal base. Hair and head coverings are put back over the garment using the pose base as a clean plate, so curls keep their shape without a tee fringe. Tee the new garment doesn't cover, and thin gaps between hair and garment, are filled from the personal base's surroundings. Output: a 1024 PNG and a 2048 JPEG.

Scans run on a separate CPU lane in the worker, so they never wait behind someone's onboarding.

## Onboarding progress and shared preparation

Garment/body presets are shared across accounts and devices. They live in the worker's local disk cache, not in each phone's browser. Changing the garment graph changes its cache keys and causes one new preparation batch; an ordinary restart reuses existing presets. The worker currently prepares the catalog before consuming onboarding jobs, so an uncached batch can delay the first queued user. The Windows GPU worker must stay online for new generations.

The setup screen separates that queue wait from personal likeness creation. Startup reports `preparing_catalog` and the number of prepared variants; none of the personal stages are marked complete at that point. Once claimed, the job reports selfie checking, body-template checking, reference preparation, likeness creation, larger-image preparation, alignment, and saving. Actual ComfyUI sampler events provide the image-creation step counter. If live events are unavailable, the worker continues with stage updates and heartbeats; the UI does not invent a percentage or a completion time. Elapsed time is measured from submission, and a delayed update is shown as an unconfirmed delay rather than a failed job.

## Masks

`services/worker/parsing.py` runs SegFormer-B2 human parsing (`mattmdjaga/segformer_b2_clothes`, ONNX, CPU, ~1 s per image, flip-averaged). Download `onnx/model.onnx` into `comfy-identity/parsing-assets/`; it is ignored by Git. **Licence:** NVIDIA SegFormer licence, non-commercial research/evaluation only. That suits this hackathon demo; replace it before commercial use.

`services/worker/compose.py` holds all mask logic:
- Printed faces and arms on garments are kept as garment by checking them against the real head and arm positions.
- Hair/head-covering, face, garment and uncovered masks.
- Alignment scores, pose locking and the clean-plate matte.

## Acceptance run (September 26, 2026)

`comfy-identity/run_personal_base_acceptance.py` uses identical settings for every person and no manual fixes. It ran nine selfies: Jon (neutral and grinning) plus seven generated test people never used for tuning.
- Long curly hair; glasses with a broad grin; hijab; 70s silver hair; long hair while laughing; thick full beard; shoulder-length locs.
- Garments: dragon tee, hooded jacket, photographic tee (printed faces); the polo was checked in the worker run.

Results (visual review by the developer; the people themselves have not reviewed them):
- Hair length and texture, beards, glasses and skin tone carried over; the hijab was kept after the covering instruction was added. Grins and laughs came out closed-mouth neutral.
- The first prompt gave Jon long hair he doesn't have. "Copy the hair exactly" fixed it.
- The first covering instruction added a cap to a person without one; the conditional wording fixed both cases.
- Garment render alignment: 0.91–0.99. Personal-base lower-body alignment is locked by construction.
- Worker timing (real ComfyUI/parser, Firebase stubbed):
  - onboarding 53–54 s warm (73 s the first time, including the one-time pose-base upscale and garment masks)
  - scans 1.35–1.56 s, including while another person's onboarding ran on the GPU
- Firebase round trips and phone upload/download are additional and not measured live.

Known limits: a faint soft seam can remain where long hair meets a garment whose shoulder sits lower than the base tee. The body and pose come from the selected template: this is an appearance preview, not an exact fit or body-shape estimate. Garments must cover at least the base tee's area. The acceptance people are synthetic; real judges' own likeness judgement is still the real test.

## Commands

```powershell
python comfy-identity/build_personal_base_workflow.py
services/worker/.venv/Scripts/python.exe comfy-identity/run_personal_base_acceptance.py --name trial `
  --garment-map garment_map.json --garments thread-2 thread-3 --person me=selfie.jpg
```

Earlier accounts without a personal base still work through the HyperSwap face-swap path (`FaceSwap_TryOn_2K.api.json`). `--pipeline qwen` restores the original trained-adapter diffusion path.
