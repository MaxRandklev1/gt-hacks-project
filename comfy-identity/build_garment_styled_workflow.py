"""Build the one-time "base model wearing this garment" graph used by fast face-swap try-ons.

Derived from the reviewed 4K try-on graph: the identity adapter, BFS head-swap LoRA and
person reference are removed, so the pose model keeps his own face and expression.
Sampling uses the Viggle turbo LoRA (6 steps) with a 0.5 MP garment reference and no texture
pass (see "Fast presets" below). Outputs are the 1024 render (node 24) and a 2K image
(node 35: Nomos 4x, then halved), which the worker caches per garment and body template.
"""
import copy
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "Qwen21_Universal_TryOn_4K.api.json"
TARGET = ROOT / "Qwen21_Garment_Styled_2K.api.json"
GARMENT_PROMPT = """Use <image1> as the base photograph. Replace only the upper garment worn in <image1> with the garment shown in <image2>. Preserve the reference garment's design, print layout and placement, colors, collar, neckline, sleeves, seams and fabric appearance. Drape that garment naturally over the existing body and pose, with plausible folds, perspective and occlusion by the hands and arms. Keep printed artwork recognizable and in the same arrangement on the garment as in <image2>.
Any photographic faces or people printed on the garment are artwork. Use <image2> only for the garment; do not transfer a reference model's face, body, pose or background.
Keep the person in <image1> unchanged: the same face, facial expression, hair, head position, skin, body shape, pose, arms and hands. Preserve <image1>'s exact framing, camera angle, pants, other lower-body clothing, footwear, accessories, lighting and background. Do not crop or reframe.
Match the base photograph's sharpness and subtle grain. Avoid smoothing, waxy skin and artificial sharpening."""

api = json.loads(SOURCE.read_text(encoding="utf-8-sig"))
source = copy.deepcopy(api)

# Drop identity-specific nodes and preview/intermediate outputs.
for node_id in ("4", "14", "15", "11", "12", "18", "25", "30"):
    del api[node_id]
api["5"]["inputs"]["model"] = ["3", 0]
encode = api["8"]["inputs"]
encode["prompt"] = GARMENT_PROMPT
encode["images.image_2"] = ["32", 0]
del encode["images.image_3"]
api["8"]["_meta"]["title"] = "Encode pose + garment / keep resized base dimensions"
api["9"]["_meta"]["title"] = "Garment render: 80 steps"
api["1"]["inputs"]["image"] = "styled_pose_base.png"
api["32"]["inputs"]["image"] = "styled_garment_reference.png"
api["24"]["inputs"]["filename_prefix"] = "Garment_Styled/final"
api["24"]["_meta"]["title"] = "FINAL - Base model wearing garment (1024)"
api["38"] = {"class_type": "ImageScaleBy", "inputs": {"upscale_method": "lanczos", "scale_by": 0.5, "image": ["34", 0]},
             "_meta": {"title": "Halve 4x upscale to 2x"}}
api["35"]["inputs"].update(images=["38", 0], filename_prefix="Garment_Styled/final_2K")
api["35"]["_meta"]["title"] = "FINAL 2K - Base model wearing garment"

# ---- Fast presets (measured ~30 s instead of 170-300 s, same look on reviewed garments) ----------
# 1. Viggle Qwen-Image-2.1 turbo LoRA (DMD distilled, v0.2.1, rank 256): 6 steps instead of 80, no CFG.
#    Licence: Qwen research. https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo
TURBO_LORA = "Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r256.safetensors"
api["50"] = {"class_type": "LoraLoaderModelOnly", "_meta": {"title": "Turbo LoRA (6-step distillation)"},
             "inputs": {"model": ["3", 0], "lora_name": TURBO_LORA, "strength_model": 1.0}}
api["5"]["inputs"]["model"] = ["50", 0]
# The model card's 6 student nodes, with the pipeline's resolution shift for a 1024x1024 latent
# (mu = 0.5 + 0.4 * (tokens - 256) / 7936, tokens = 64 * 64). Every body template resizes to 1024x1024.
NODES, TOKENS = (1.0, 0.9375, 0.875, 0.75, 0.5, 0.25), 64 * 64
MU = 0.5 + 0.4 * (TOKENS - 256) / (8192 - 256)
SIGMAS = ", ".join(f"{math.exp(MU) / (math.exp(MU) + (1 / t - 1)):.6f}" for t in NODES) + ", 0.0"
api["51"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}}
api["52"] = {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["8", 0]}}
api["53"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
api["54"] = {"class_type": "ManualSigmas", "_meta": {"title": "Turbo 6-step schedule (1024x1024)"}, "inputs": {"sigmas": SIGMAS}}
api["9"] = {"class_type": "SamplerCustomAdvanced", "_meta": {"title": "Garment render: turbo, 6 steps"},
            "inputs": {"noise": ["51", 0], "guider": ["52", 0], "sampler": ["53", 0], "sigmas": ["54", 0], "latent_image": ["8", 2]}}
# 2. Garment reference capped at 0.5 MP: large product photos made every step ~2x slower with no visible gain.
api["55"] = {"class_type": "ImageScaleToTotalPixels", "_meta": {"title": "Garment reference - 0.5 MP"},
             "inputs": {"image": ["32", 0], "upscale_method": "lanczos", "megapixels": 0.5, "resolution_steps": 32}}
encode["images.image_2"] = ["55", 0]
# 3. No texture pass: it cost more time than the whole turbo render, with no visible difference at 6 steps.
for node_id in ("19", "20", "21", "22", "23", "27", "28", "29"):
    del api[node_id]
api["24"]["inputs"]["images"] = ["17", 0]
api["37"]["inputs"]["image"] = ["17", 0]


def validate():
    for node_id, node in api.items():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in api, f"Node {node_id} references removed node {value[0]}"
    assert not any(node["class_type"].startswith("UniversalIdentity") for node in api.values())
    assert not any(node["class_type"] == "LoraLoaderModelOnly" and "bfs" in node["inputs"]["lora_name"] for node in api.values())
    # Loaders, decode, resize and upscale stay as reviewed; sampling is the measured turbo setup.
    for node_id in ("3", "6", "7", "16", "17", "31", "33", "34"):
        assert api[node_id]["inputs"] == source[node_id]["inputs"], f"Unintended change to node {node_id}"
    assert api["9"]["class_type"] == "SamplerCustomAdvanced" and api["50"]["inputs"]["lora_name"] == TURBO_LORA
    assert len(SIGMAS.split(",")) == 7 and api["8"]["inputs"]["images.image_2"] == ["55", 0]
    assert [node_id for node_id, node in api.items() if node["class_type"] == "SaveImage"] == ["24", "35"]


validate()
TARGET.write_text(json.dumps(api, indent=2), encoding="utf-8")
print(json.dumps({"api": str(TARGET), "nodes": len(api), "outputs": {"1024": "24", "2K": "35"}}, indent=2))
