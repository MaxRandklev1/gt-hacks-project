"""Build the close-up face pass used after the full-body personal base.

In a full-body 1024 image the face is only ~150-180 px wide, which caps likeness. This pass takes
a head crop enlarged to ~1024 px (image1) and redraws the head from the selfie (image2) at that
size with the same person-neutral head-swap instructions. The crop is blended back afterwards.

One run saves two versions: the redrawn crop (node 24) and the same crop with a local HyperSwap
identity polish from the selfie (node 26), so both can be compared without a second GPU pass.
"""
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "Qwen21_Personal_Base_2K.api.json"
TARGET = ROOT / "Qwen21_Face_Pass_1024.api.json"
CLOSE_UP = """This is a close-up crop of a person's head and shoulders; keep the crop's framing, scale and head position exactly.
"""

source = json.loads(SOURCE.read_text(encoding="utf-8-sig"))
keep = ("1", "3", "4", "5", "6", "7", "8", "9", "14", "15", "16", "17", "44")
api = {node_id: copy.deepcopy(source[node_id]) for node_id in keep}
api["1"]["inputs"]["image"] = "face_pass_crop.png"
api["1"]["_meta"] = {"title": "Head crop, enlarged to ~1024 px"}
api["8"]["inputs"]["images.image_1"] = ["1", 0]  # Crop is already a multiple of 32; no resize.
# Head-swap LoRA stays at 0.65: at 1.0 it invented long hair on a short-haired test person.
api["44"]["inputs"]["megapixels"] = 1.0          # Full-detail selfie reference.
api["9"]["inputs"]["steps"] = 40
api["15"]["inputs"]["edit_instructions"] = CLOSE_UP + source["15"]["inputs"]["edit_instructions"]
api["24"] = {"class_type": "SaveImage", "inputs": {"images": ["17", 0], "filename_prefix": "FacePass/redrawn"},
             "_meta": {"title": "Close-up redrawn"}}
api["25"] = {"class_type": "AdvancedSwapFaceImage", "_meta": {"title": "Local HyperSwap identity polish"}, "inputs": {
    "source_images": ["14", 1], "target_image": ["17", 0], "api_token": "-1",
    "face_swapper_model": "hyperswap_1c_256", "face_detector_model": "scrfd", "pixel_boost": "1024x1024",
    "face_occluder_model": "none", "face_parser_model": "bisenet_resnet_34", "face_mask_blur": 0.3,
    "face_selector_mode": "one", "face_position": 0, "sort_order": "large-small", "score_threshold": 0.3,
    "use_box_mask": True, "use_occlusion_mask": False, "use_area_mask": False, "use_region_mask": False,
    "face_mask_areas": "upper-face,lower-face,mouth", "face_mask_regions": "skin,nose,mouth,upper-lip,lower-lip",
    "face_mask_padding": "0,0,0,0"}}
api["26"] = {"class_type": "SaveImage", "inputs": {"images": ["25", 0], "filename_prefix": "FacePass/polished"},
             "_meta": {"title": "Close-up redrawn + HyperSwap polish"}}

for node_id, node in api.items():
    for value in node["inputs"].values():
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
            assert value[0] in api, f"Node {node_id} references missing node {value[0]}"
TARGET.write_text(json.dumps(api, indent=2), encoding="utf-8")
print(json.dumps({"api": str(TARGET), "nodes": len(api), "outputs": {"redrawn": "24", "polished": "26"}}, indent=2))
