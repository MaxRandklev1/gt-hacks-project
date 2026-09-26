"""Build a separate, reusable identity + upper-garment try-on workflow."""
import copy
import json
from pathlib import Path
import uuid


ROOT = Path(__file__).resolve().parent
STEM = "Qwen21_Universal_TryOn"
BASE_IMAGE = "tryon_pose_base.png"
GARMENT_IMAGE = "tryon_shirt_reference.png"
TRYON_PROMPT = """head_swap: Use <image1> as the base photograph. Replace the head's identity with the person shown in <image2>. Take recognizable facial anatomy and appearance only from <image2>, while preserving the head position, scale, rotation, gaze and facial expression of <image1>.
Expression comes from <image1>: match its mouth opening, lip position, mouth corners, cheek tension, eyelids and eyebrows. Reconstruct the reference identity with that expression, rather than copying the expression from <image2>.
Replace only the upper garment in <image1> with the garment shown in <image3>. Preserve the reference garment's design, print layout and placement, colors, collar, neckline, sleeves, seams and fabric appearance. Drape that garment naturally over the existing body and pose, with plausible folds, perspective and occlusion by the hands and arms. Keep printed artwork recognizable and in the same arrangement on the garment as in <image3>.
Any photographic faces or people printed on the garment are artwork, not identity references for the person wearing it. Use <image3> only for the garment; do not transfer a reference model's face, body, pose or background.
Preserve <image1>'s exact framing, camera angle, body shape, pose, arms, hands, pants, other lower-body clothing, footwear, accessories, lighting and background. Do not crop or reframe. Match exposed skin to the reference person's underlying skin tone while retaining local shadows, highlights and natural color variation.
Preserve natural skin texture, fine facial detail and visible age-related features. Match the base photograph's sharpness and subtle grain. Avoid smoothing, beauty retouching, waxy skin and artificial sharpening. Keep a natural transition between the head and neck."""

workflow = json.loads((ROOT / "Qwen21_Universal_Identity_TwoPass.json").read_text(encoding="utf-8-sig"))
api = json.loads((ROOT / "Qwen21_Universal_Identity_TwoPass.api.json").read_text(encoding="utf-8-sig"))
source_api = copy.deepcopy(api)
nodes = {node["id"]: node for node in workflow["nodes"]}
assert not ({31, 32} & nodes.keys()), "New try-on IDs already exist in the source workflow"
next_link = max(link[0] for link in workflow["links"])


def title(node_id, value):
    nodes[node_id]["title"] = value
    if str(node_id) in api:
        api[str(node_id)].setdefault("_meta", {})["title"] = value


def widgets(node_id, values):
    nodes[node_id]["widgets_values_named"] = copy.deepcopy(values)
    nodes[node_id]["widgets_values"] = list(values.values())
    if str(node_id) in api:
        api[str(node_id)]["inputs"].update({name: value for name, value in values.items()
                                           if name not in {"upload", "control_after_generate"}})


def connect(source, source_slot, target, target_name):
    """Add a connection or replace its source while keeping serialized links consistent."""
    global next_link
    target_slot = next(i for i, port in enumerate(nodes[target]["inputs"]) if port["name"] == target_name)
    port = nodes[target]["inputs"][target_slot]
    if port["link"] is not None:
        link = next(link for link in workflow["links"] if link[0] == port["link"])
        nodes[link[1]]["outputs"][link[2]]["links"].remove(link[0])
        link[1:3] = [source, source_slot]
        link_id = link[0]
    else:
        next_link += 1
        link_id = next_link
        workflow["links"].append([link_id, source, source_slot, target, target_slot,
                                  nodes[source]["outputs"][source_slot]["type"]])
        port["link"] = link_id
    nodes[source]["outputs"][source_slot]["links"].append(link_id)
    api[str(target)]["inputs"][target_name] = [str(source), source_slot]


title(1, "1 - POSE BASE: framing, body and background")
widgets(1, {"image": BASE_IMAGE, "upload": "image"})

# ImageScaleToTotalPixels schema: image, upscale_method, megapixels, resolution_steps.
# Multiples of 32 match Qwen's latent grid and avoid another geometry adjustment.
resize = {
    "id": 31, "type": "ImageScaleToTotalPixels", "title": "Pose base - 1 MP / preserve aspect ratio",
    "pos": [30, 520], "size": [390, 160], "flags": {}, "order": 31, "mode": 0,
    "inputs": [
        {"localized_name": "image", "name": "image", "type": "IMAGE", "link": None},
        *[{"localized_name": name, "name": name, "type": port_type,
           "widget": {"name": name}, "link": None}
          for name, port_type in [("upscale_method", "COMBO"), ("megapixels", "FLOAT"),
                                  ("resolution_steps", "INT")]],
    ],
    "outputs": [{"localized_name": "IMAGE", "name": "IMAGE", "type": "IMAGE", "links": []}],
    "properties": {"Node name for S&R": "ImageScaleToTotalPixels"},
}
workflow["nodes"].append(resize)
nodes[31] = resize
api["31"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {}, "_meta": {"title": resize["title"]}}
widgets(31, {"upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 32})
connect(1, 0, 31, "image")
connect(31, 0, 8, "images.image_1")
connect(31, 0, 12, "image_a")

garment = copy.deepcopy(nodes[1])
garment.update(id=32, pos=[1020, 40], size=[520, 600], order=32)
for output in garment["outputs"]:
    output["links"] = []
workflow["nodes"].append(garment)
nodes[32] = garment
api["32"] = {"class_type": "LoadImage", "inputs": {}}
title(32, "3 - GARMENT: upload upper-garment reference")
widgets(32, {"image": GARMENT_IMAGE, "upload": "image"})
connect(32, 0, 8, "images.image_3")

# Keep the native autogrow UI's next empty image input after the three references.
empty_slot = next(i for i, port in enumerate(nodes[8]["inputs"]) if port["name"] == "images.image_3") + 1
nodes[8]["inputs"].insert(empty_slot, {"localized_name": "image_4", "name": "images.image_4",
                                     "shape": 7, "type": "IMAGE", "link": None})
for link in workflow["links"]:
    if link[3] == 8 and link[4] >= empty_slot:
        link[4] += 1
title(8, "Encode pose + person + garment / keep resized base dimensions")
title(15, "4 - Reusable identity + garment instructions")
widgets(15, {"edit_instructions": TRYON_PROMPT, "expression_mode": "Neutral (closed mouth)",
             "expression_details": ""})
title(9, "5 - Identity + garment render: 80 steps")
title(11, "SAVE - First-pass identity + garment")
widgets(11, {"filename_prefix": "Universal_TryOn/identity_garment"})
title(12, "Resized pose base / identity + garment")
widgets(18, {"filename_prefix": "Universal_TryOn/baseline"})
title(24, "FINAL - Try-on + refined subject / clean background")
widgets(24, {"filename_prefix": "Universal_TryOn/final"})

intro = """# Reusable person + garment try-on
1. POSE BASE sets framing, pose, body and scene. Its aspect ratio is preserved at about 1 MP.
2. In PERSON, select a saved person or upload a photo folder. Choose a reference thumbnail. Train once if you want a personal adapter; training starts only when you click Train identity.
3. In GARMENT, upload the upper garment you want the person to wear. This is a separate reference from the person's photos.
4. Run. The first pass combines identity from image2 with the garment from image3. Neutral (closed mouth) is selected for this neutral pose base to avoid copying a smile from the identity reference. For another base, choose Match base image or Custom as needed. The second pass gently refines the foreground over its clean first-pass background.

The prompt works with any saved person and garment. Faces printed on clothing are artwork, not the wearer's identity. Printed graphics and lettering may be approximated; this visual try-on does not establish exact physical fit or sizing. Inspect the first-pass and final comparisons."""
widgets(13, {"text": intro})
title(13, "Start here - person, pose and garment")

refinement_note = nodes[26]["widgets_values_named"]["text"]
refinement_note = refinement_note.replace("# Two-pass identity + texture workflow", "# Try-on foreground refinement")
refinement_note = refinement_note.replace(
    "PASS 1 is unchanged from the existing identity workflow: head-swap 0.65, person 0.80, 80 steps. Its original output is still saved by node 11.",
    "PASS 1 combines the selected person's identity and the upper garment on the resized pose base, using head-swap 0.65, person 0.80 and 80 steps. Node 11 saves that identity + garment result.",
)
refinement_note += "\nThe second pass retains the garment from the first pass. Check printed details against the garment reference: generated artwork and lettering may differ from the source."
widgets(26, {"text": refinement_note})

# Keep all three user inputs visible across the top; model setup stays below the base.
for node_id, position in {
    3: [30, 740], 4: [30, 885], 6: [30, 1060], 7: [30, 1260],
    15: [1590, 40], 8: [1590, 760], 9: [2270, 40], 5: [2270, 440],
    10: [2270, 650], 11: [2680, 40], 12: [2680, 710], 13: [470, 1180],
}.items():
    nodes[node_id]["pos"] = position
nodes[15]["size"] = [620, 650]
nodes[13]["size"] = [1740, 270]
workflow["groups"][0].update(title="PASS 1 - Pose + saved person + garment", bounding=[0, -20, 3360, 1510])
workflow.update(id=str(uuid.uuid4()), revision=0, last_node_id=32, last_link_id=next_link)
workflow["extra"] = {"ds": {"scale": 0.31, "offset": [100, 110]}}


def validate():
    # Only inputs/first-pass instructions and output names change; preserve all generation settings.
    for node_id in (3, 4, 5, 6, 7, 9, 10, 14, 16, 17, 19, 20, 21, 22, 23, 25, 27, 28, 29, 30):
        assert api[str(node_id)]["inputs"] == source_api[str(node_id)]["inputs"], f"Unintended change to node {node_id}"
    assert api["8"]["inputs"]["images.image_1"] == ["31", 0]
    assert api["8"]["inputs"]["images.image_2"] == ["14", 1]
    assert api["8"]["inputs"]["images.image_3"] == ["32", 0]
    assert api["12"]["inputs"]["image_a"] == ["31", 0]
    assert api["22"]["inputs"]["latent_image"] == ["9", 0]
    assert api["9"]["inputs"]["steps"] == 80
    assert api["22"]["inputs"]["steps"] == 40 and api["22"]["inputs"]["denoise"] == 0.04
    assert api["19"]["inputs"]["strength_model"] == 0.20
    assert api["24"]["inputs"]["images"] == ["29", 0]
    assert api["15"]["inputs"]["expression_mode"] == "Neutral (closed mouth)"
    link_ids = {link[0] for link in workflow["links"]}
    assert len(link_ids) == len(workflow["links"])
    for link_id, source, source_slot, target, target_slot, link_type in workflow["links"]:
        output = nodes[source]["outputs"][source_slot]
        port = nodes[target]["inputs"][target_slot]
        assert link_id in output["links"] and port["link"] == link_id
        assert output["type"] == port["type"] == link_type
        assert api[str(target)]["inputs"][port["name"]] == [str(source), source_slot]
    for node in workflow["nodes"]:
        for output in node.get("outputs", []):
            assert set(output.get("links") or []).issubset(link_ids)


validate()
for suffix, data in [(".json", workflow), (".api.json", api)]:
    (ROOT / f"{STEM}{suffix}").write_text(json.dumps(data, indent=2), encoding="utf-8")
print(json.dumps({"workflow": str(ROOT / f"{STEM}.json"), "api": str(ROOT / f"{STEM}.api.json"),
                  "new_nodes": {"pose_resize": 31, "garment_reference": 32},
                  "nodes": len(workflow["nodes"]), "links": len(workflow["links"]),
                  "validation": "Three references wired; generation and foreground composite settings preserved"}, indent=2))
