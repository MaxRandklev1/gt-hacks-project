# Fast face-swap try-on

The app's default path. It replaces per-user LoRA training and per-scan diffusion with two steps:

1. **Once per garment (before the demo):** `Qwen21_Garment_Styled_2K.api.json` dresses the fixed base model in the garment. The identity adapter, BFS head-swap LoRA and person reference are removed, so the model keeps his own face and neutral expression. The reviewed refinement/composite pass is unchanged. Outputs: the original 1024 render and a 2K image (Nomos 4×, then halved). The worker caches both under `.local/firebase-worker/styled/`.
2. **Per scan:** `FaceSwap_TryOn_2K.api.json` swaps the account's selfie onto both cached renders with FaceFusion HyperSwap (`hyperswap_1c_256`, local ONNX, `api_token = -1`). It returns a 1024 PNG and a 2K JPEG.

HyperSwap transfers **facial identity only**. Expression, gaze, head pose, hair, lighting, body and background all come from the garment render. A smiling or funny-face selfie still produces the model's closed-mouth neutral expression, as if the person had stood in for the model at the same shoot.

## Onboarding

Version-5 onboarding is height/weight, one selfie and consent. The `enroll` job checks that the selfie contains one clear face, then saves it with a schema-4 manifest (`mode: faceswap`) under `users/{uid}/identity/{jobId}/`. No photo set, training or GPU work is involved. Earlier trained identities remain usable: their saved reference selfie becomes the swap source.

## Builders

```powershell
python comfy-identity/build_garment_styled_workflow.py   # derived from Qwen21_Universal_TryOn_4K.api.json
python comfy-identity/build_faceswap_workflow.py         # settings from DeepFake.json
```

Both graphs need the **Facefusion_comfyui** custom node (its HyperSwap, SCRFD, ArcFace and BiSeNet ONNX models download on first use) in addition to the existing try-on assets.

## Worker

The worker starts on `--pipeline faceswap`, polls every second, and on startup renders any active garment without a cached render, then warms the swap models. A garment added later renders on its first scan, which takes a few minutes once. `--no-prewarm` skips startup rendering; `--pipeline qwen` restores the earlier trained-adapter diffusion path.

## Limits

- Hair, head shape, neck, arms and skin tone below the face stay the model's. Beards transfer partially.
- One body and pose for every garment; this is a visual preview, not a fit estimate.
- The swap model works at 256 px internally, with pixel boost to 512 (1024 image) or 1024 (2K image). Very small, blurry or side-on selfies weaken likeness.
