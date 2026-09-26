"""Build the onboarding graph that turns one selfie into a person's "personal base".

The personal base is the fixed pose image with the person's whole head (face, hairstyle and
length, facial hair, head covering, glasses) and their skin tone on exposed skin, with a
neutral closed-mouth expression. Clothing, body, pose and background stay the base's, so the
cached garment renders can later be composited onto it.

Derived from the reviewed Qwen21_Universal_Identity_TwoPass graph (BFS head-swap LoRA, identity
reference, texture pass and foreground composite). Adds the same 1 MP base resize used by the
garment renders and saves 1024 + 2K for both the result and the resized base.
"""
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "Qwen21_Universal_Identity_TwoPass.api.json"
TARGET = ROOT / "Qwen21_Personal_Base_2K.api.json"
PROMPT = """head_swap: Use <image1> as the base photograph. Replace the whole head's appearance with the person shown in <image2>: their facial anatomy, face shape, skin tone, apparent age, hairstyle, hair length, hair texture and hair color, facial hair, eyebrows, glasses and any head covering. Keep the head position, scale, rotation and gaze of <image1>.
Only if the person in <image2> wears a head covering, keep it exactly as worn, with the same type, color, fabric and coverage of the head, hair, ears and neck; do not remove it or reveal hair it covers. If <image2> shows no head covering, do not add one.
Copy the hair exactly as it appears in <image2>: the same length, volume, hairline, parting and style. Do not lengthen, shorten or restyle it. Hair or a head covering that reaches past the neck in <image2> falls naturally over the shoulders and upper garment of <image1>.
Match all exposed skin, including the neck, arms and hands, to the reference person's underlying skin tone while retaining local shadows, highlights and natural color variation.
Preserve <image1>'s body shape, pose, clothing, accessories, framing, lighting and background. Do not crop or reframe.
Preserve natural skin texture, fine facial detail and visible age-related features. Match the base photograph's sharpness and subtle grain. Avoid smoothing, beauty retouching, waxy skin and artificial sharpening. Keep a natural transition between the head and neck."""

api = json.loads(SOURCE.read_text(encoding="utf-8-sig"))
source = copy.deepcopy(api)
for node_id in ("11", "12", "18", "25", "30"):  # Previews and intermediate saves.
    del api[node_id]
api["1"]["inputs"]["image"] = "personal_pose_base.png"
api["31"] = {"class_type": "ImageScaleToTotalPixels", "_meta": {"title": "Pose base - 1 MP (matches garment renders)"},
             "inputs": {"upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 32, "image": ["1", 0]}}
api["8"]["inputs"]["images.image_1"] = ["31", 0]
# The worker crops the selfie to head and shoulders; a small reference keeps sampling fast
# (a full-size selfie roughly triples per-step time) without losing facial detail.
api["44"] = {"class_type": "ImageScaleToTotalPixels", "_meta": {"title": "Selfie reference - 0.35 MP"},
             "inputs": {"upscale_method": "lanczos", "megapixels": 0.35, "resolution_steps": 32, "image": ["14", 1]}}
api["8"]["inputs"]["images.image_2"] = ["44", 0]
api["14"]["inputs"].update(profile_id="personal-base-profile", reference_index=0, use_trained_identity=False)
api["15"]["inputs"].update(edit_instructions=PROMPT, expression_mode="Neutral (closed mouth)", expression_details="")
api["24"]["inputs"]["filename_prefix"] = "Personal_Base/final"
api["33"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xNomosUniDAT_otf.safetensors"}}
for rgb, upscale, half, save, image, prefix in (("37", "34", "38", "35", ["29", 0], "Personal_Base/final_2K"),
                                                ("39", "40", "41", "42", ["31", 0], "Personal_Base/base_2K")):
    api[rgb] = {"class_type": "SplitImageWithAlpha", "inputs": {"image": image}}
    api[upscale] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["33", 0], "image": [rgb, 0]}}
    api[half] = {"class_type": "ImageScaleBy", "inputs": {"upscale_method": "lanczos", "scale_by": 0.5, "image": [upscale, 0]}}
    api[save] = {"class_type": "SaveImage", "inputs": {"images": [half, 0], "filename_prefix": prefix}}
api["43"] = {"class_type": "SaveImage", "inputs": {"images": ["31", 0], "filename_prefix": "Personal_Base/base"}}


def validate():
    for node_id, node in api.items():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in api, f"Node {node_id} references removed node {value[0]}"
    for node_id in ("3", "4", "5", "6", "7", "9", "16", "17", "19", "20", "21", "22", "23", "27", "28", "29"):
        assert api[node_id]["inputs"] == source[node_id]["inputs"], f"Unintended change to node {node_id}"
    assert sorted(n for n, node in api.items() if node["class_type"] == "SaveImage") == ["24", "35", "42", "43"]


validate()
TARGET.write_text(json.dumps(api, indent=2), encoding="utf-8")
print(json.dumps({"api": str(TARGET), "nodes": len(api),
                  "outputs": {"personal_1024": "24", "personal_2K": "35", "base_1024": "43", "base_2K": "42"}}, indent=2))
