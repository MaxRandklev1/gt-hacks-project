"""ComfyUI node entry point for local, reusable identity profiles."""
import logging

from .backend import ProfileStore, TrainingManager, selected_photos, register_routes

STORE = ProfileStore()
MANAGER = TrainingManager(STORE)
WEB_DIRECTORY = "./web"


class UniversalIdentityProfile:
    @classmethod
    def INPUT_TYPES(cls):
        try:
            profiles = [profile["id"] for profile in STORE.list_profiles()]
        except (OSError, ValueError, KeyError):
            profiles = []
        return {"required": {
            "model": ("MODEL",),
            "profile_id": (profiles or ["(upload a profile)"],),
            "reference_index": ("INT", {"default": 0, "min": 0, "max": 2147483647}),
            "identity_strength": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 2.0, "step": 0.05}),
            "use_trained_identity": ("BOOLEAN", {"default": True}),
        }}

    RETURN_TYPES = ("MODEL", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("model", "reference_image", "trigger", "identity_instruction")
    FUNCTION = "apply_identity"
    CATEGORY = "identity/local"
    DESCRIPTION = "Choose a person's local photo profile. Applies its trained adapter when available; otherwise uses reference-only identity guidance."

    @classmethod
    def VALIDATE_INPUTS(cls, profile_id, reference_index, **kwargs):
        try:
            if MANAGER.active_job():
                return "Identity training is using the GPU. Wait for completion before generating."
            STORE.reference_path(STORE.load(profile_id), reference_index)
        except (OSError, ValueError, KeyError) as error:
            return str(error)
        return True

    @classmethod
    def IS_CHANGED(cls, profile_id, **kwargs):
        try:
            profile = STORE.load(profile_id)
            adapter = STORE.adapter_path(profile)
            reference = STORE.reference_path(profile, kwargs.get("reference_index", 0))
            return ((STORE.profile_dir(profile_id) / "profile.json").stat().st_mtime_ns,
                    adapter.stat().st_mtime_ns if adapter else None, reference.stat().st_mtime_ns,
                    (profile.get("inference_reference") or {}).get("sha256"))
        except (OSError, ValueError):
            return float("nan")

    def apply_identity(self, model, profile_id, reference_index, identity_strength, use_trained_identity):
        if MANAGER.active_job():
            raise RuntimeError("Identity training is using the GPU. Wait for completion before generating.")
        import numpy as np
        import torch
        from PIL import Image
        profile = STORE.load(profile_id)
        with Image.open(STORE.reference_path(profile, reference_index)) as image:
            image_tensor = torch.from_numpy(np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0).unsqueeze(0)
        adapter = STORE.adapter_path(profile) if use_trained_identity and identity_strength > 0 else None
        if adapter is not None:
            import comfy.sd
            import comfy.utils
            weights = comfy.utils.load_torch_file(str(adapter), safe_load=True)
            model, _ = comfy.sd.load_lora_for_models(model, None, weights, identity_strength, 0)
            instruction = f"The person in reference image 2 is {profile['trigger']}. Use that person's identity and distinctive facial features."
            status = "Trained identity adapter applied."
        else:
            instruction = "Use the identity and distinctive facial features of the person in reference image 2."
            status = "Reference-only mode: no trained identity adapter applied."
        logging.info("Universal identity %s: %s", profile["id"], status)
        return {"ui": {"text": [status]}, "result": (model, image_tensor, profile["trigger"], instruction)}


class UniversalIdentityPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "identity_instruction": ("STRING", {"forceInput": True}),
            "edit_instructions": ("STRING", {"multiline": True, "default":
                "head_swap: Use <image1> as the base. Replace the head's identity with the person shown in <image2>. Take recognizable facial anatomy and appearance from <image2>, while preserving the head position, scale, rotation, gaze and facial expression of <image1>. Expression comes from <image1>: match its mouth opening, lip position, mouth corners, cheek tension, eyelids and eyebrows. Reconstruct the reference identity with that expression. Preserve <image1>'s body shape, pose, clothing, accessories, framing, lighting and background. Match exposed skin to the reference person's underlying skin tone while retaining local shadows, highlights and natural color variation. Preserve natural skin texture, fine facial detail and visible age-related features. Match the base photograph's sharpness and subtle grain. Avoid smoothing, beauty retouching, waxy skin and artificial sharpening. Keep a natural transition between head and neck."}),
            "expression_mode": (["Match base image", "Neutral (closed mouth)", "Custom"], {"default": "Match base image"}),
            "expression_details": ("STRING", {"multiline": True, "default": ""}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "build_prompt"
    CATEGORY = "identity/local"

    def build_prompt(self, identity_instruction, edit_instructions, expression_mode="Match base image", expression_details=""):
        parts = [edit_instructions.strip(), identity_instruction.strip()]
        if expression_mode == "Neutral (closed mouth)":
            parts.append("Expression override: give the person a neutral expression with a fully closed mouth, gently resting lips, relaxed mouth corners and cheeks, relaxed eyelids and eyebrows, and no smile or visible teeth. Preserve the reference person's identity while reconstructing this neutral expression.")
        elif expression_mode == "Custom" and expression_details.strip():
            parts.append("Expression override: " + expression_details.strip())
        return ("\n".join(parts),)


NODE_CLASS_MAPPINGS = {"UniversalIdentityProfile": UniversalIdentityProfile, "UniversalIdentityPrompt": UniversalIdentityPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {"UniversalIdentityProfile": "Universal Identity · Photos & Training", "UniversalIdentityPrompt": "Universal Identity · Edit Instructions"}

from server import PromptServer
if PromptServer.instance is not None:
    register_routes(PromptServer.instance, STORE, MANAGER)
