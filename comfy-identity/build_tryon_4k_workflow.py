"""Add a model-only 4x upscale and build an independent existing-image upscaler."""
import copy
import json
from pathlib import Path
import uuid


ROOT = Path(__file__).resolve().parent
UPSCALER = "4xNomosUniDAT_otf.safetensors"
FULL_STEM = "Qwen21_Universal_TryOn_4K"
ONLY_STEM = "Qwen21_TryOn_Upscale_Only"
source_workflow = json.loads((ROOT / "Qwen21_Universal_TryOn.json").read_text(encoding="utf-8-sig"))
source_api = json.loads((ROOT / "Qwen21_Universal_TryOn.api.json").read_text(encoding="utf-8-sig"))
source_nodes = {node["id"]: node for node in source_workflow["nodes"]}


class Graph:
    def __init__(self, workflow, api):
        self.workflow = workflow
        self.api = api
        self.nodes = {node["id"]: node for node in workflow["nodes"]}
        self.next_link = max((link[0] for link in workflow["links"]), default=0)

    def add(self, node_id, node_type, title, position, size, inputs, outputs, values=None):
        values = values or {}
        node = {
            "id": node_id, "type": node_type, "title": title, "pos": position, "size": size,
            "flags": {}, "order": len(self.nodes), "mode": 0,
            "inputs": [{"localized_name": name, "name": name, "type": port_type, "link": None,
                        **({"widget": {"name": name}} if name in values else {})}
                       for name, port_type in inputs],
            "outputs": [{"localized_name": name, "name": name, "type": port_type, "links": []}
                        for name, port_type in outputs],
            "properties": {"Node name for S&R": node_type},
        }
        self.workflow["nodes"].append(node)
        self.nodes[node_id] = node
        self.api[str(node_id)] = {"class_type": node_type, "inputs": {}, "_meta": {"title": title}}
        self.widgets(node_id, values)
        return node

    def clone(self, template_id, node_id, title, position, size):
        node = copy.deepcopy(source_nodes[template_id])
        node.update(id=node_id, title=title, pos=position, size=size, order=len(self.nodes), mode=0)
        for port in node.get("inputs", []):
            port["link"] = None
        for port in node.get("outputs", []):
            port["links"] = []
        self.workflow["nodes"].append(node)
        self.nodes[node_id] = node
        if str(template_id) in source_api:
            self.api[str(node_id)] = {"class_type": node["type"], "inputs": {}, "_meta": {"title": title}}
        return node

    def widgets(self, node_id, values):
        self.nodes[node_id]["widgets_values_named"] = copy.deepcopy(values)
        self.nodes[node_id]["widgets_values"] = list(values.values())
        if str(node_id) in self.api:
            self.api[str(node_id)]["inputs"].update({name: value for name, value in values.items()
                                                    if name != "upload"})

    def connect(self, source, source_slot, target, target_name):
        target_slot = next(i for i, port in enumerate(self.nodes[target]["inputs"])
                           if port["name"] == target_name)
        assert self.nodes[target]["inputs"][target_slot]["link"] is None
        self.next_link += 1
        output = self.nodes[source]["outputs"][source_slot]
        self.workflow["links"].append([self.next_link, source, source_slot, target, target_slot, output["type"]])
        output["links"].append(self.next_link)
        self.nodes[target]["inputs"][target_slot]["link"] = self.next_link
        self.api[str(target)]["inputs"][target_name] = [str(source), source_slot]

    def upscale(self, model_id, upscale_id, image_id, position):
        x, y = position
        self.add(model_id, "UpscaleModelLoader", "4x upscale model", [x, y], [440, 100],
                 [("model_name", "COMBO")], [("UPSCALE_MODEL", "UPSCALE_MODEL")], {"model_name": UPSCALER})
        self.add(upscale_id, "ImageUpscaleWithModel", "Upscale 4x / preserve aspect ratio", [x + 510, y], [410, 100],
                 [("upscale_model", "UPSCALE_MODEL"), ("image", "IMAGE")], [("IMAGE", "IMAGE")])
        self.connect(model_id, 0, upscale_id, "upscale_model")
        self.connect(image_id, 0, upscale_id, "image")

    def save(self, stem):
        link_ids = {link[0] for link in self.workflow["links"]}
        assert len(link_ids) == len(self.workflow["links"])
        for link_id, source, source_slot, target, target_slot, link_type in self.workflow["links"]:
            output = self.nodes[source]["outputs"][source_slot]
            port = self.nodes[target]["inputs"][target_slot]
            assert link_id in output["links"] and port["link"] == link_id
            assert output["type"] == port["type"] == link_type
            assert self.api[str(target)]["inputs"][port["name"]] == [str(source), source_slot]
        for node in self.nodes.values():
            for output in node.get("outputs", []):
                assert set(output.get("links") or []).issubset(link_ids)
        self.workflow.update(id=str(uuid.uuid4()), revision=0,
                             last_node_id=max(self.nodes), last_link_id=self.next_link)
        for suffix, data in [(".json", self.workflow), (".api.json", self.api)]:
            (ROOT / f"{stem}{suffix}").write_text(json.dumps(data, indent=2), encoding="utf-8")


full = Graph(copy.deepcopy(source_workflow), copy.deepcopy(source_api))
assert not ({33, 34, 35, 36} & full.nodes.keys()), "Upscale IDs already exist in the source"
full.upscale(33, 34, 29, [30, 3930])
full.clone(24, 35, "FINAL 4K - Saved 4x try-on", [1110, 3930], [1120, 1000])
full.widgets(35, {"filename_prefix": "Universal_TryOn/final_4K"})
full.connect(34, 0, 35, "images")
full.clone(13, 36, "4x upscale - no additional sampling", [30, 4140], [970, 280])
full.widgets(36, {"text": """# Stage 3 - Final 4x upscale
The composited final image from stage 2 is enlarged with 4xNomosUniDAT. A 1024 x 1024 input becomes 4096 x 4096; other aspect ratios are retained at four times the width and height.
This stage adds no diffusion sampling and no face-restoration pass. The original 1024 image remains saved above, and the enlarged version is saved under Universal_TryOn/final_4K.
To enlarge an existing finished image without running the try-on stages, open Qwen21_TryOn_Upscale_Only."""})
full.workflow["groups"].append({"id": max(group["id"] for group in full.workflow["groups"]) + 1,
                               "title": "STAGE 3 - Final 4x upscale", "bounding": [0, 3850, 2290, 1140],
                               "color": "#705c80", "font_size": 28, "flags": {}})
assert all(full.api[node_id] == original for node_id, original in source_api.items())
assert full.workflow["links"][:len(source_workflow["links"])] == source_workflow["links"]
assert full.api["34"]["inputs"]["image"] == ["29", 0]
assert full.api["24"] == source_api["24"]
full.save(FULL_STEM)

only = Graph({"id": "", "revision": 0, "last_node_id": 0, "last_link_id": 0,
              "nodes": [], "links": [], "groups": [], "config": {},
              "extra": {"ds": {"scale": 0.70, "offset": [60, 80]}}, "version": 0.4}, {})
only.clone(1, 1, "INPUT - Existing finished image", [30, 40], [440, 550])
only.widgets(1, {"image": "tryon_final_1024.png", "upload": "image"})
only.upscale(2, 3, 1, [540, 40])
only.clone(24, 4, "FINAL 4K - Upscaled existing image", [1510, 40], [800, 940])
only.widgets(4, {"filename_prefix": "Universal_TryOn/upscaled_existing_4K"})
only.connect(3, 0, 4, "images")
only.clone(13, 5, "Upscale an existing image", [540, 240], [900, 280])
only.widgets(5, {"text": """# Enlarge an existing finished image
Upload a completed image on the left, then Run. This workflow only loads the 4x upscaler and enlarges that image; it does not run the 80-step identity/garment pass or the 40-step refinement pass.
4xNomosUniDAT preserves the source aspect ratio at four times the width and height: 1024 x 1024 becomes 4096 x 4096. There is no separate face restoration.
The output is saved under Universal_TryOn/upscaled_existing_4K."""})
only.workflow["groups"] = [{"id": 1, "title": "Existing image - 4x upscale only",
                            "bounding": [0, -30, 2370, 1070], "color": "#705c80",
                            "font_size": 28, "flags": {}}]
assert {node["class_type"] for node in only.api.values()} == {
    "LoadImage", "UpscaleModelLoader", "ImageUpscaleWithModel", "SaveImage"}
only.save(ONLY_STEM)
print(json.dumps({"full": str(ROOT / f"{FULL_STEM}.json"), "full_api": str(ROOT / f"{FULL_STEM}.api.json"),
                  "upscale_only": str(ROOT / f"{ONLY_STEM}.json"),
                  "upscale_only_api": str(ROOT / f"{ONLY_STEM}.api.json"),
                  "full_node_ids": {"model": 33, "upscale": 34, "save": 35, "note": 36},
                  "upscale_only_node_ids": {"image": 1, "model": 2, "upscale": 3, "save": 4, "note": 5},
                  "validation": "Existing graph preserved; both upscale paths verified without model execution"}, indent=2))
