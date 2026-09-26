# Universal identity and garment preview

Workflow: `Qwen21_Universal_TryOn.json`. API export: `Qwen21_Universal_TryOn.api.json`.

For automatic 4096 × 4096 output, use `Qwen21_Universal_TryOn_4K.json`. To upscale an existing render quickly, use `Qwen21_TryOn_Upscale_Only.json`. See `UPSCALE_4K_README.md` for the tested models and detail comparisons.

## Inputs

- **Fixed pose:** the ChatGPT-generated white-shirt model in `ClothesSwap/ChatGPT Image Sep 26, 2026, 01_31_17 PM.png`. The workflow resizes it to about one megapixel for the local GPU.
- **Person:** the existing photo-folder/profile panel. Jon is selected for the initial example. Select another saved person or upload their photo folder and train their identity profile using the existing controls.
- **Garment:** `ClothesSwap/Screenshot 2026-09-26 125422.png`. Replace the image in the garment upload node to use a different shirt.

The pose, framing, and body shape come from the fixed model image. The person profile supplies the wearer's identity and exposed skin appearance. The garment image supplies the shirt design. The prompt separates faces printed on a garment from the wearer's identity and does not hardcode a person's physical features or the shirt's colors or artwork.

## Processing

The first generation uses all three references with the existing BFS and selected identity adapters. It changes identity and the upper garment together while preserving the pose, lower clothing, hands, and scene. The expression control defaults to **Neutral (closed mouth)** for this neutral base; it can be changed independently.

The second pass uses the existing restrained texture correction. A local BiRefNet mask composites the refined foreground over the first pass's background. Both the first-pass baseline and final image are saved so garment artwork and skin can be compared.

The generated garment is a visual approximation: complex prints, logos, and lettering should be checked against the uploaded reference. The fixed model body is not a measurement or fit estimate for the selected person.

## Compare trained profiles

`compare_identity_profiles.py` renders existing trained profiles through the same full try-on/4K graph, normally changing only the profile ID and output prefixes. It retains the original-resolution baseline/final images, 4K images, submitted graphs and run history under ignored `training-comparisons/`. It does not update the app's defaults or publish personal images.

```powershell
python comfy-identity/compare_identity_profiles.py --profiles PROFILE_BEFORE PROFILE_AFTER --labels "Before" "After" --name profile-comparison
```

Run when ComfyUI is idle and coordinate with the cloud worker before submitting local comparisons. Existing comparison names cannot submit again. Use the same arguments plus `--collect-only` to resume collection without running either render again; keep ComfyUI running until unfinished outputs have been collected. Optional `--face-box LEFT TOP RIGHT BOTTOM --reference PATH` creates matching face crops from the saved 4K outputs, with the crop measured in original-resolution pixels. The original reference is shown separately and is not edited.

More than two profiles are supported, with one label per profile. `--no-personal-lora 0` disables the personal adapter for the first case while retaining BFS and the image reference. `--pin-identity-instruction` uses the profile's normal trained-identity wording for every case, including disabled adapters, so removing a LoRA does not also change the prompt. For a controlled step-count comparison, separately trained profiles must share the same photo pixels/order, captions, trigger, seed and training hyperparameters. A no-personal-LoRA control does not bypass the deployed app's training requirement; production behavior remains unchanged.

The Jon 400-step versus fast 80-step comparison uses identical training-photo pixels and order, reference photo, core training hyperparameters and inference settings. The old profile used detailed training captions; the new profile uses generic captions and a different identity trigger. It is a practical profile comparison, not an isolated test of training-step count. A single image pair cannot establish equal likeness across people or poses.

Both complete comparison renders succeeded on September 26, 2026, saving original 1024 × 1024 and 4096 × 4096 results. ComfyUI reported 256.69 seconds for the 400-step profile render and 259.68 seconds for the 80-step profile render; fewer **training** steps do not reduce the unchanged image-generation work. The matched graphs use seed 42, 80 generation steps, 40 refinement steps, identity strength 0.8 and BFS strength 0.65. Private outputs and the reference/full-image/face comparison boards remain in `training-comparisons/jon-400-vs-80/` and are excluded from Git. Initial visual review found similar output quality on this pose, with remaining artificial skin/eye detail; likeness still needs the person's review.

### Controlled lower-step training

Two isolated profiles were trained using the exact five photos, order, generic captions, identity trigger, seed and training settings of the 80-step profile. Only the step count and private file paths differed; the original profile and adapter hashes remained unchanged.

| Personal training | Measured local training-job time |
| --- | ---: |
| 20 steps | 46.49 seconds |
| 40 steps | 57.02 seconds |
| 80 steps | 90.57 seconds |

These are individual local runs, including model setup and saving, excluding cloud transfers, photo selection, queue delays and image generation. Model setup/launcher overhead alone was about 31–38 seconds across these runs, so runtime does not scale directly with step count. The local 20/40-step evidence is in ignored `.local/low-step-training.json`; the 80-step evidence is `.local/timing80.json`.

The no-personal-LoRA control retains BFS, the same reference image and pinned identity-prompt wording. It loads no personal adapter. Installed adapter-key mapping, finite/nonzero trained tensors, load logs and between-run cache resets were checked to rule out an obvious loading/cache explanation for similar images. Disabling personal weights does not remove the pretrained base model, BFS, reference encoding, refinement or upscale work. This is a local experiment; deployed onboarding remains at 80 steps.

All three new full renders completed successfully, preserving original 1024 × 1024 and 4096 × 4096 outputs. Their graph inputs matched after normalizing only profile ID, the enabled/disabled personal-adapter flag and output filenames. The prior 80-step render used the same resolved identity instruction, references, seed and rendering settings and was reused as the baseline. The user judged the no-personal-LoRA result equally good for Jon's demo. Visual review of 20/40/80 found no obvious additional likeness benefit on this single pose/reference; small color and shading differences remain. This supports trying reference-only onboarding, not a claim of equivalence across identities, poses or references. Image generation still took roughly 4 minutes 13 seconds to 4 minutes 20 seconds per new case.

Private artifacts are in `training-comparisons/jon-controlled-low-steps/`: `low-step-faces.png` and `low-step-full.png` compare no personal LoRA, 20, 40 and 80 steps; `no-lora-vs-80-faces.png` is the matched two-way close-up. Reproduce new render cases with three profile IDs and `--no-personal-lora 0 --pin-identity-instruction`, plus `--face-box 350 15 690 320` for this fixed pose. The trained cases should be fresh isolated clones of the 80-step profile with identical captions and trigger, never modifications to the user's saved profile.

## Validation

- The first three-reference render completed successfully in about 121 seconds at 1024 × 1024. The garment's blue sleeves/collar and large photograph transferred recognizably; the hands, pocket pose, jeans, and framing remained close to the base.
- The first preview copied the identity reference's smile despite the general base-expression instruction. The final preset therefore uses the stronger **Neutral (closed mouth)** expression override already available in the identity workflow.
- **Verified:** the full workflow was launched with ComfyUI's Run button and completed successfully at 1024 × 1024. The neutral-expression override removed the broad smile; the garment print, pose, hands, and clean background remained recognizable after refinement.
- The UI's generation and compositing inputs matched the API export. Final run ID: `57af83b0-6995-4e1b-9f95-b5ba1864b0af`. Exact submitted inputs and history are `tryon-results/neutral_full_ui.*`.
- Final render: `tryon-results/TryOn_final.png`. Source/result comparison: `tryon-results/TryOn_comparison.png`. The fixed-VAE baseline and original first-pass output are also retained there. This is one tested person/garment combination, not a guarantee of equal fidelity for all inputs.
