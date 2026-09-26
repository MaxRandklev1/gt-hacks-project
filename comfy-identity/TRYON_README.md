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

## Validation

- The first three-reference render completed successfully in about 121 seconds at 1024 × 1024. The garment's blue sleeves/collar and large photograph transferred recognizably; the hands, pocket pose, jeans, and framing remained close to the base.
- The first preview copied the identity reference's smile despite the general base-expression instruction. The final preset therefore uses the stronger **Neutral (closed mouth)** expression override already available in the identity workflow.
- **Verified:** the full workflow was launched with ComfyUI's Run button and completed successfully at 1024 × 1024. The neutral-expression override removed the broad smile; the garment print, pose, hands, and clean background remained recognizable after refinement.
- The UI's generation and compositing inputs matched the API export. Final run ID: `57af83b0-6995-4e1b-9f95-b5ba1864b0af`. Exact submitted inputs and history are `tryon-results/neutral_full_ui.*`.
- Final render: `tryon-results/TryOn_final.png`. Source/result comparison: `tryon-results/TryOn_comparison.png`. The fixed-VAE baseline and original first-pass output are also retained there. This is one tested person/garment combination, not a guarantee of equal fidelity for all inputs.
