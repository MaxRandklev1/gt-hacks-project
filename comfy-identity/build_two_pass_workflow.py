"""Add an independent texture pass to the saved universal identity workflow."""
import copy
import json
from pathlib import Path
import uuid


ROOT = Path(__file__).resolve().parent
STEM = "Qwen21_Universal_Identity_TwoPass"
DETAILER = "elusarcas-qwen2-1-detailer-v1_gguf_split.safetensors"
FIXED_VAE = "texture_fix_vae_for_qwen_image_2.1_bf16.safetensors"
DETAILER_STRENGTH = 0.20
SECOND_PASS_DENOISE = 0.04
BACKGROUND_REMOVAL_MODEL = "birefnet.safetensors"
TEXTURE_PROMPT = """enhance this image. Use <image1> as the complete visual reference. Preserve the exact person's identity, facial anatomy and proportions, head position, gaze, mouth shape and facial expression. Preserve their underlying skin tone and melanin-related coloration, age-related features, hairstyle and existing distinctive features.
Preserve the body, pose, clothing, accessories, framing, lighting and background. Keep the same image dimensions and composition.
Reduce excessive glossy or waxy skin smoothing while retaining natural highlights and local shadows. Restore subtle, realistic tonal variation and natural texture supported by the image. Keep detail restrained and consistent with the image's original sharpness and grain. Do not invent freckles, blemishes, scars or additional wrinkles, and do not add exaggerated or sharpened pores. Avoid beauty retouching, skin lightening, oversharpening and changes to facial structure."""

workflow = json.loads((ROOT / "Qwen21_Universal_Identity.json").read_text(encoding="utf-8-sig"))
api = json.loads((ROOT / "Qwen21_Universal_Identity.api.json").read_text(encoding="utf-8-sig"))
original_api = copy.deepcopy(api)
original_links = copy.deepcopy(workflow["links"])
nodes = {node["id"]: node for node in workflow["nodes"]}
next_link = max(link[0] for link in workflow["links"])


def clone(template_id, node_id, title, position, size=None):
    node = copy.deepcopy(nodes[template_id])
    node.update(id=node_id, title=title, pos=position, order=node_id, mode=0)
    if size:
        node["size"] = size
    for port in node.get("inputs", []):
        port["link"] = None
    for port in node.get("outputs", []):
        port["links"] = []
    workflow["nodes"].append(node)
    nodes[node_id] = node
    if str(template_id) in api:
        source_inputs = api[str(template_id)]["inputs"]
        api[str(node_id)] = {
            "class_type": node["type"],
            "inputs": {key: copy.deepcopy(value) for key, value in source_inputs.items()
                       if not (isinstance(value, list) and len(value) == 2
                               and isinstance(value[0], str) and isinstance(value[1], int))},
            "_meta": {"title": title},
        }
    return node


def widgets(node_id, values):
    node = nodes[node_id]
    node["widgets_values_named"] = copy.deepcopy(values)
    node["widgets_values"] = list(values.values())
    if str(node_id) in api:
        api[str(node_id)]["inputs"].update({key: value for key, value in values.items()
                                           if key != "control_after_generate"})


def native(node_id, node_type, title, position, size, input_types, output_types, values=None):
    """Create a native node using the installed object_info schema's port order."""
    values = values or {}
    node = {
        "id": node_id, "type": node_type, "title": title, "pos": position, "size": size,
        "flags": {}, "order": node_id, "mode": 0,
        "inputs": [
            {"localized_name": name, "name": name, "type": port_type, "link": None,
             **({"widget": {"name": name}} if name in values else {})}
            for name, port_type in input_types
        ],
        "outputs": [{"localized_name": name, "name": name, "type": port_type, "links": []}
                    for name, port_type in output_types],
        "properties": {"Node name for S&R": node_type},
    }
    workflow["nodes"].append(node)
    nodes[node_id] = node
    api[str(node_id)] = {"class_type": node_type, "inputs": {}, "_meta": {"title": title}}
    widgets(node_id, values)
    return node


def connect(source, source_slot, target, target_name):
    global next_link
    next_link += 1
    target_slot = next(index for index, port in enumerate(nodes[target]["inputs"])
                       if port["name"] == target_name)
    port_type = nodes[source]["outputs"][source_slot]["type"]
    workflow["links"].append([next_link, source, source_slot, target, target_slot, port_type])
    nodes[source]["outputs"][source_slot]["links"].append(next_link)
    nodes[target]["inputs"][target_slot]["link"] = next_link
    api[str(target)]["inputs"][target_name] = [str(source), source_slot]


# Decode the first latent with the texture-fix VAE for both the baseline and reference.
clone(7, 16, "PASS 2 - Texture-fix VAE", [30, 1600], [390, 100])
widgets(16, {"vae_name": FIXED_VAE})
clone(10, 17, "First-pass latent / texture-fix decode", [470, 1600], [620, 90])
connect(9, 0, 17, "samples")
connect(16, 0, 17, "vae")
clone(11, 18, "BASELINE - Fixed VAE only", [1580, 1600], [540, 530])
widgets(18, {"filename_prefix": "Universal_Identity_TwoPass/fixed_vae_only"})
connect(17, 0, 18, "images")

# Start from the unpatched loader, not the BFS or personal identity model branch.
clone(4, 19, "PASS 2 - Detailer strength (clean model)", [30, 1790], [390, 140])
widgets(19, {"lora_name": DETAILER, "strength_model": DETAILER_STRENGTH})
connect(3, 0, 19, "model")
clone(5, 20, "PASS 2 - Independent reference cache", [30, 1990], [390, 100])
widgets(20, {"device": "auto", "dtype": "default"})
connect(19, 0, 20, "model")

encoder = clone(8, 21, "PASS 2 - Texture edit / preserve this identity", [470, 1760], [620, 430])
encoder["inputs"] = [port for port in encoder["inputs"] if port["name"] != "images.image_3"]
widgets(21, {"prompt": TEXTURE_PROMPT, "negative_prompt": "", "resolution": 0})
connect(6, 0, 21, "clip")
connect(16, 0, 21, "vae")
connect(17, 0, 21, "images.image_1")

clone(9, 22, f"PASS 2 - Subtle texture pass / denoise {SECOND_PASS_DENOISE:.2f}", [1150, 1600], [340, 330])
widgets(22, {"seed": 42, "control_after_generate": "fixed", "steps": 40, "cfg": 1.0,
             "sampler_name": "euler", "scheduler": "simple", "denoise": SECOND_PASS_DENOISE})
connect(20, 0, 22, "model")
connect(21, 0, 22, "positive")
connect(21, 1, 22, "negative")
connect(9, 0, 22, "latent_image")  # Encoder output 2 is an empty latent, not the edited image.

clone(10, 23, "PASS 2 - Final texture-fix decode", [1150, 1990], [340, 90])
connect(22, 0, 23, "samples")
connect(16, 0, 23, "vae")

# Estimate the subject from the clean baseline, then keep its original background.
native(27, "LoadBackgroundRemovalModel", "Subject mask - BiRefNet", [30, 2250], [390, 100],
       [("bg_removal_name", "COMBO")], [("bg_model", "BACKGROUND_REMOVAL")],
       {"bg_removal_name": BACKGROUND_REMOVAL_MODEL})
native(28, "RemoveBackground", "Find subject in clean baseline", [470, 2250], [620, 100],
       [("bg_removal_model", "BACKGROUND_REMOVAL"), ("image", "IMAGE")], [("mask", "MASK")])
connect(27, 0, 28, "bg_removal_model")
connect(17, 0, 28, "image")
native(29, "ImageCompositeMasked", "Refined subject + clean background", [1150, 2250], [340, 220],
       [("destination", "IMAGE"), ("source", "IMAGE"), ("x", "INT"), ("y", "INT"),
        ("resize_source", "BOOLEAN"), ("mask", "MASK")], [("IMAGE", "IMAGE")],
       {"x": 0, "y": 0, "resize_source": False})
nodes[29]["inputs"][-1]["shape"] = 7  # Optional mask socket in the native node schema.
connect(17, 0, 29, "destination")
connect(23, 0, 29, "source")
connect(28, 0, 29, "mask")
native(30, "MaskPreview", "Subject mask - white receives refinement", [30, 2450], [660, 570],
       [("mask", "MASK")], [("mask", "MASK")])
connect(28, 0, 30, "mask")

clone(11, 24, "FINAL - Refined subject / clean background", [2190, 1600], [540, 530])
widgets(24, {"filename_prefix": "Universal_Identity_TwoPass/final"})
connect(29, 0, 24, "images")
clone(12, 25, "COMPARE - Clean baseline / refined subject", [1580, 2250], [1150, 840])
connect(17, 0, 25, "image_a")
connect(29, 0, 25, "image_b")

note = f"""# Two-pass identity + texture workflow
PASS 1 is unchanged from the existing identity workflow: head-swap 0.65, person 0.80, 80 steps. Its original output is still saved by node 11.
PASS 2 below starts from that actual edited latent and uses a clean model branch with only the Detailer. The texture-fix VAE supplies the reference decode, second-pass reference encoding and final decode.

The final image applies the refinement only to the automatically detected foreground subject. BiRefNet finds that subject in the clean fixed-VAE baseline. Its soft mask blends the refined subject over the baseline background, so background texture from the second pass is excluded. No extra edge feathering is applied. Inspect the mask preview: white receives refinement, black keeps the baseline, and gray softly blends the two.

Compare three saved outputs: original first pass; fixed-VAE-only baseline; foreground-refined final. The slider compares the clean baseline (A) with the final composite (B).

Start with Detailer strength {DETAILER_STRENGTH:.2f} and denoise {SECOND_PASS_DENOISE:.2f}. These refinement settings and this exact prompt were tested together. Higher denoise added rough texture in the comparison runs, so raise it carefully. These are local test settings, not the author's recommended preset. Lower denoise if identity, expression or color drifts. Set second-pass denoise to 0 to bypass resampling and keep the fixed-VAE result. Strength 0 removes the Detailer but still permits base-model denoising.
At CFG 1, preservation instructions belong in the editable positive prompt. The sampler still performs 40 low-noise steps at denoise {SECOND_PASS_DENOISE:.2f}; the setting changes the noise range rather than reducing the step count.
The existing identity/profile controls remain above. The texture prompt works for any selected person and must not invent facial features, blemishes or exaggerated pores."""
clone(13, 26, "Foreground refinement controls and comparison", [30, 3100], [1480, 650])
widgets(26, {"text": note})

workflow["groups"] = [
    {"id": 1, "title": "PASS 1 - Existing identity workflow", "bounding": [0, -20, 2780, 1490],
     "color": "#49667d", "font_size": 28, "flags": {}},
    {"id": 2, "title": "PASS 2 - Foreground refinement / preserve baseline background", "bounding": [0, 1520, 2780, 2280],
     "color": "#5e7160", "font_size": 28, "flags": {}},
]
workflow.update(id=str(uuid.uuid4()), revision=0, last_node_id=30, last_link_id=next_link)
workflow["extra"] = {"ds": {"scale": 0.31, "offset": [100, 110]}}


def validate():
    assert all(api[node_id] == original for node_id, original in original_api.items()), "First pass changed"
    assert workflow["links"][:len(original_links)] == original_links, "First-pass links changed"
    assert api["4"]["inputs"]["strength_model"] == 0.65
    assert api["14"]["inputs"]["identity_strength"] == 0.8
    assert api["9"]["inputs"]["steps"] == 80
    assert api["19"]["inputs"]["model"] == ["3", 0]
    assert api["22"]["inputs"]["latent_image"] == ["9", 0]
    assert api["21"]["inputs"]["images.image_1"] == ["17", 0]
    prompt_port = next(port for port in nodes[21]["inputs"] if port["name"] == "prompt")
    assert prompt_port["link"] is None, "Second-pass prompt must remain an editable widget"
    assert prompt_port["widget"] == {"name": "prompt"}
    assert nodes[21]["widgets_values"][0] == nodes[21]["widgets_values_named"]["prompt"] == TEXTURE_PROMPT
    assert api["19"]["inputs"]["strength_model"] == DETAILER_STRENGTH
    assert api["22"]["inputs"]["denoise"] == SECOND_PASS_DENOISE
    assert api["28"]["inputs"]["image"] == ["17", 0]
    assert api["29"]["inputs"] == {"x": 0, "y": 0, "resize_source": False,
                                      "destination": ["17", 0], "source": ["23", 0], "mask": ["28", 0]}
    assert api["24"]["inputs"]["images"] == ["29", 0]
    assert api["25"]["inputs"]["image_b"] == ["29", 0]
    assert not nodes[21]["outputs"][2]["links"], "Empty encoder latent must remain unused"
    link_ids = {link[0] for link in workflow["links"]}
    assert len(link_ids) == len(workflow["links"])
    for link_id, source, output_slot, target, input_slot, link_type in workflow["links"]:
        assert link_id in nodes[source]["outputs"][output_slot]["links"]
        assert nodes[target]["inputs"][input_slot]["link"] == link_id
        assert nodes[source]["outputs"][output_slot]["type"] == link_type
        name = nodes[target]["inputs"][input_slot]["name"]
        assert api[str(target)]["inputs"][name] == [str(source), output_slot]
    for node in workflow["nodes"]:
        for port in node.get("outputs", []):
            assert set(port.get("links") or []).issubset(link_ids)


validate()
for filename, value in [(f"{STEM}.json", workflow), (f"{STEM}.api.json", api)]:
    (ROOT / filename).write_text(json.dumps(value, indent=2), encoding="utf-8")
print(json.dumps({
    "workflow": str(ROOT / f"{STEM}.json"),
    "api": str(ROOT / f"{STEM}.api.json"),
    "nodes": len(workflow["nodes"]), "links": len(workflow["links"]),
    "node_ids": {"fixed_vae": 16, "fixed_first_decode": 17, "fixed_vae_save": 18,
                 "detailer": 19, "second_cache": 20, "second_encode": 21,
                 "second_sampler": 22, "second_decode": 23, "final_save": 24, "comparison": 25,
                 "background_removal_model": 27, "subject_mask": 28, "foreground_composite": 29,
                 "mask_preview": 30},
    "validation": "First pass preserved; refinement latent and foreground-only composite paths verified",
}, indent=2))
