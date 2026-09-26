# Personal-base try-on (default)

Goal: an unfamiliar person uploads one selfie and sees themselves (face, hair or head covering, beard, glasses, skin tone) in each catalog garment, in the model's pose, with a neutral expression. Nothing is prepared per person ahead of time.

## How it works

1. **Once per garment (worker startup):** `Qwen21_Garment_Styled_2K.api.json` dresses the fixed base model in the garment. Output 1024 + 2K, cached under `.local/firebase-worker/styled/`. Its garment mask is computed once on the CPU.
2. **Once per person (onboarding, GPU, ~50 s warm):**
   - The selfie is parsed and cropped to head and shoulders. With no usable face, the user is asked to retake it.
   - `Qwen21_Personal_Base_2K.api.json` (BFS head-swap LoRA, selfie as reference, person-neutral prompt, neutral closed-mouth expression, 24 steps, texture pass skipped) regenerates the whole head and exposed skin on the pose base.
   - Everything outside the head/hair/skin edit region is locked back to the pose base, so the garment renders line up exactly. The worker checks lower-body alignment.
   - The resulting **personal base** plus its hair/covering and tee masks are saved privately (`users/{uid}/identity/{jobId}/personal-*`). The app shows it as "This is you in the fitting room", with a retake option.
3. **Per scan (CPU only, ~1.4 s of worker time):** the garment region of the render is composited onto the personal base. Hair and head coverings are put back over the garment using the pose base as a clean plate, so curls keep their shape without a tee fringe. Tee the new garment doesn't cover, and thin gaps between hair and garment, are filled from the personal base's surroundings. Output: a 1024 PNG and a 2048 JPEG.

Scans run on a separate CPU lane in the worker, so they never wait behind someone's onboarding.

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

Known limits: a faint soft seam can remain where long hair meets a garment whose shoulder sits lower than the base tee. The body, pose and build are the base model's: this is an appearance preview, not a fit or body-shape estimate. Garments must cover at least the base tee's area. The acceptance people are synthetic; real judges' own likeness judgement is still the real test.

## Commands

```powershell
python comfy-identity/build_personal_base_workflow.py
services/worker/.venv/Scripts/python.exe comfy-identity/run_personal_base_acceptance.py --name trial `
  --garment-map garment_map.json --garments thread-2 thread-3 --person me=selfie.jpg
```

Earlier accounts without a personal base still work through the HyperSwap face-swap path (`FaceSwap_TryOn_2K.api.json`). `--pipeline qwen` restores the original trained-adapter diffusion path.
