# BFS Head V1 — Qwen Image 2.1

[`bfs_head_v1_qwen_2.1.safetensors`](../bfs_head_v1_qwen_2.1.safetensors) is the BFS head-swap
LoRA trained for [`Qwen/Qwen-Image-2.1`](https://huggingface.co/Qwen/Qwen-Image-2.1).

This release is separate from the Qwen Image Edit 2509/2511 weights. Use the Qwen Image 2.1
base model and the included workflow:

[`workflows/Head Swap V1 Qwen 2.1 Workflow.json`](../workflows/Head%20Swap%20V1%20Qwen%202.1%20Workflow.json)

## Inputs

The workflow uses two images:

- **Image 1 / `<image1>`:** the target or base image. Its body, pose, composition, lighting, and
  environment provide the foundation for the result.
- **Image 2 / `<image2>`:** the reference face/head. Its identity, hair, eye color, nose
  structure, and other visible head features are transferred.

Keep this order. Reversing the images changes which person is treated as the target.

## Prompt

The included workflow uses this starting prompt:

```text
head_swap: start with <image1> as the base image, keeping its lighting, environment, and background. remove the head from <image1> completely and replace it with the head from <image2>, strictly preserving the hair, eye color, nose structure from <image2>. copy the direction of the eye, head rotation, micro expressions from <image1>, high quality, sharp details, 4k
```

Replace `<image1>` and `<image2>` only if your node or workflow uses a different reference-token
notation. Keep the edit localized to the head when the body and background should remain unchanged.

## ComfyUI setup

1. Put `bfs_head_v1_qwen_2.1.safetensors` in `ComfyUI/models/loras/`.
2. Load the Qwen Image 2.1 diffusion model, Qwen3-VL text encoder, and matching Qwen Image 2.1
   VAE required by the workflow.
3. Open the included workflow and connect the target image to Image 1 and the reference head to
   Image 2.
4. Start with LoRA strength `1.0` and adjust only after checking the identity, hair boundary,
   skin blending, and expression transfer.

The workflow also contains an optional Pruna 8-step acceleration LoRA. It is not part of BFS and
can be disabled if it is not installed.

## Resolution and quality

Qwen Image 2.1 supports native high-resolution generation. The included workflow starts from a
moderate resolution and supports custom sizing in multiples of 32. For this head-swap use case,
larger output sizes can help retain facial detail and reduce the broad color or softness shifts
that may appear at lower resolutions. Higher resolution does not guarantee perfect preservation.

## Examples

<p align="center">
  <img src="../images/bfs_qwen_2.1_1.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_2.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_3.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_4.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_5.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_6.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_7.png" width="49%" />
  <img src="../images/bfs_qwen_2.1_8.png" width="49%" />
</p>

## Limitations and responsible use

Small faces, extreme angles, occlusion, hands over the face, hair boundaries, and strong lighting
changes can reduce fidelity. Always inspect the result for unintended changes to the body,
clothing, background, text, and identity. Do not use or share results involving public figures or
people who have not given consent.

[Back to the repository index](../README.md)
