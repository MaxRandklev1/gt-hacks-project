# 4K try-on output

Default upscaler: **4xNomosUniDAT_otf**. It enlarges the final 1024 × 1024 try-on image to **4096 × 4096** using ComfyUI's native tiled neural upscaler. It does not add another diffusion or face-restoration pass. The learned upscaler still estimates fine detail, and does not recover an exact original photograph or correct pre-existing garment-print inaccuracies.

## Workflows

- `Qwen21_Universal_TryOn_4K.json`: complete try-on graph with the 4× stage appended after the final foreground composite. Saves the existing 1024 output and a separate `Universal_TryOn/final_4K` PNG. Person/profile, garment upload, prompts, and generation settings are unchanged.
- `Qwen21_TryOn_Upscale_Only.json`: upload an existing final image and Run. The current example is `tryon_final_1024.png`. This avoids rerunning the identity/garment generation.

The node uses the model's 4× scale and preserves aspect ratio. This fixed square base produces 4096 × 4096; changing the upstream dimensions changes the resulting dimensions proportionally.

## Local comparison and assets

Both candidate upscalers successfully produced 4096 × 4096 PNGs from the exact saved final image. Matched face and shirt crops are retained under `tryon-results/`. Visual review preferred Nomos for finer hair and more natural skin/eye detail. The original 1024 image remains unchanged.

- **Nomos:** [author model card](https://huggingface.co/Phips/4xNomosUniDAT_otf), revision `fce2ce83d597c3f79cb0671702901049c5af0959`, file `4xNomosUniDAT_otf.safetensors`. Verified SHA-256: `29a4e94fca48e25f458c81abcb7c2c223eda5aa7c73ba76eb6df226bfdeb69a9`. Comfy execution time: **13.87 seconds**. Selected output: `tryon-results/TryOn_4K_Nomos.png`.
- **Real-ESRGAN:** [official release](https://github.com/xinntao/Real-ESRGAN/releases/tag/v0.1.0), file `RealESRGAN_x4plus.pth`. Downloaded-file SHA-256: `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1`. Execution time: **4.43 seconds**. Alternative retained for comparison.

Both models are installed in ComfyUI's shared `models/upscale_models` directory. Native execution uses overlapping tiles and reduces tile size if required for VRAM. No extra custom node or server restart was required.

The complete graph's original nodes and links are checked by `build_tryon_4k_workflow.py`; the appended upscale path uses the same tested native nodes as the standalone graph.

The saved upscale-only workflow was also run through ComfyUI's Run button successfully: `309ba40f-c31c-42d5-92af-91f5a59d3d46`. Its preview and model selection were saved. The complete 4K graph opens without missing-node/model errors and retains the person/profile controls; the unchanged diffusion stages were not rerun for this upscale request.
