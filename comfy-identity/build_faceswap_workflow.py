"""Build the per-scan face-swap graph: the account selfie onto a cached garment render.

HyperSwap replaces only facial identity. Expression, gaze, head pose, hair, lighting and
everything outside the face come from the garment render (the base model), so a smiling or
funny-face selfie still produces the model's neutral expression. The 1024 and 2K renders are
swapped independently so both keep native detail. Settings match DeepFake.json.
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "FaceSwap_TryOn_2K.api.json"


def swap(target, pixel_boost, title):
    return {"class_type": "AdvancedSwapFaceImage", "_meta": {"title": title}, "inputs": {
        "source_images": ["1", 0], "target_image": [target, 0], "api_token": "-1",  # -1 = local ONNX, no remote API.
        "face_swapper_model": "hyperswap_1c_256", "face_detector_model": "scrfd", "pixel_boost": pixel_boost,
        "face_occluder_model": "none", "face_parser_model": "bisenet_resnet_34", "face_mask_blur": 0.3,
        "face_selector_mode": "one", "face_position": 0, "sort_order": "large-small", "score_threshold": 0.3,
        "use_box_mask": True, "use_occlusion_mask": False, "use_area_mask": False, "use_region_mask": False,
        "face_mask_areas": "upper-face,lower-face,mouth", "face_mask_regions": "skin,nose,mouth,upper-lip,lower-lip",
        "face_mask_padding": "0,0,0,0"}}


api = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "faceswap_selfie.png"}, "_meta": {"title": "Account selfie (identity only)"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "faceswap_styled_1024.png"}, "_meta": {"title": "Garment render 1024"}},
    "3": {"class_type": "LoadImage", "inputs": {"image": "faceswap_styled_2k.png"}, "_meta": {"title": "Garment render 2K"}},
    "4": swap("2", "512x512", "Swap face / 1024"),
    "5": swap("3", "1024x1024", "Swap face / 2K"),
    "6": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "FaceSwap_TryOn/final"}, "_meta": {"title": "FINAL - 1024"}},
    "7": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "FaceSwap_TryOn/final_2K"}, "_meta": {"title": "FINAL - 2K"}},
}
TARGET.write_text(json.dumps(api, indent=2), encoding="utf-8")
print(json.dumps({"api": str(TARGET), "nodes": len(api), "outputs": {"1024": "6", "2K": "7"}}, indent=2))
