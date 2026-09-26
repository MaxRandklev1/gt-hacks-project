"""Render local identity profiles with otherwise identical try-on settings.

Outputs, graphs, and private profile metadata stay in ignored training-comparisons/.
This compares complete profiles, not necessarily training-step counts alone.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from services.worker.comfy import ComfyClient, safe_id  # noqa: E402


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def board(items, output):
    width, height, heading = 768, 768, 60
    columns = 2 if len(items) == 4 else min(3, len(items))
    rows = (len(items) + columns - 1) // columns
    canvas = Image.new("RGB", (width * columns, (height + heading) * rows), "#f5f3ee")
    draw = ImageDraw.Draw(canvas)
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    font = ImageFont.truetype(str(font_path), 24) if font_path.exists() else ImageFont.load_default()
    for index, (label, path) in enumerate(items):
        x, y = (index % columns) * width, (index // columns) * (height + heading)
        with Image.open(path) as source:
            image = ImageOps.contain(source.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
        canvas.paste(image, (x + (width - image.width) // 2, y + heading + (height - image.height) // 2))
        draw.text((x + 22, y + 18), label, fill="#20231e", font=font)
    canvas.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", required=True)
    parser.add_argument("--labels", nargs="+", help="One display label per profile.")
    parser.add_argument("--no-personal-lora", nargs="*", type=int, default=[], help="Zero-based cases to render without a personal LoRA; BFS and the image reference remain enabled.")
    parser.add_argument("--pin-identity-instruction", action="store_true", help="Use the trained identity instruction even in no-LoRA cases, isolating the weight change from prompt wording.")
    parser.add_argument("--name", required=True, help="Unique output folder name; existing runs are never resubmitted.")
    parser.add_argument("--collect-only", action="store_true", help="Resume collection without submitting GPU work.")
    parser.add_argument("--face-box", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"), help="Optional matching crop, measured in the original final image's pixels.")
    parser.add_argument("--reference", type=Path, help="Optional original reference photo shown beside the face crops.")
    args = parser.parse_args()
    name = safe_id(args.name)
    profiles = [safe_id(value) for value in args.profiles]
    labels = args.labels or (["Before", "After"] if len(profiles) == 2 else profiles)
    if len(labels) != len(profiles) or any(index < 0 or index >= len(profiles) for index in args.no_personal_lora):
        parser.error("Provide one label per profile and valid zero-based no-LoRA case indices.")
    destination = ROOT / "training-comparisons" / name
    manifest_path = destination / "manifest.json"
    client = ComfyClient()
    if args.collect_only:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["profiles"] != profiles:
            raise ValueError("These profiles do not match the saved comparison.")
    else:
        if destination.exists():
            raise ValueError("Comparison already exists. Use --collect-only; do not submit twice.")
        if not client.idle():
            raise RuntimeError("ComfyUI is busy. Wait before submitting comparisons.")
        public_profiles = [client.profile(profile) for profile in profiles]
        if any(not profile.get("training", {}).get("adapter_available") for index, profile in enumerate(public_profiles) if index not in args.no_personal_lora):
            raise ValueError("Every trained case needs a successfully trained adapter.")
        template = json.loads((ROOT / "Qwen21_Universal_TryOn_4K.api.json").read_text(encoding="utf-8-sig"))
        destination.mkdir(parents=True)
        manifest = {"profiles": profiles, "labels": labels, "profileSnapshots": public_profiles, "cases": [],
                    "noPersonalLora": args.no_personal_lora, "pinnedIdentityInstruction": args.pin_identity_instruction,
                    "template": "Qwen21_Universal_TryOn_4K.api.json",
                    "templateSha256": hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest(),
                    "limitation": "A complete-profile comparison; training captions and identity trigger may differ."}
        save_json(manifest_path, manifest)
        for index, (profile, label) in enumerate(zip(profiles, labels)):
            graph = copy.deepcopy(template)
            graph["14"]["inputs"]["profile_id"] = profile
            graph["14"]["inputs"]["use_trained_identity"] = index not in args.no_personal_lora
            if args.pin_identity_instruction:
                trigger = public_profiles[index]["trigger"]
                graph["15"]["inputs"]["identity_instruction"] = f"The person in reference image 2 is {trigger}. Use that person's identity and distinctive facial features."
            for node_id, node in graph.items():
                if node["class_type"] == "SaveImage":
                    node["inputs"]["filename_prefix"] = f"IdentityComparison/{name}/case{index}_node{node_id}"
            save_json(destination / f"case{index}.api.json", graph)
            # Never retry a submission: a lost response may still mean GPU work was queued.
            client_id = "comparison-" + hashlib.sha256(f"{name}:{index}".encode()).hexdigest()[:24]
            prompt_id = client.submit(graph, client_id)
            manifest["cases"].append({"profile": profile, "label": label, "promptId": prompt_id, "submittedAt": time.time()})
            save_json(manifest_path, manifest)
            print(json.dumps({"queued": label, "promptId": prompt_id}), flush=True)
    if len(manifest["cases"]) != len(profiles):
        raise RuntimeError("Submission was incomplete. Inspect ComfyUI before attempting any replacement work.")
    for index, case in enumerate(manifest["cases"]):
        if case.get("status") == "completed" and all(Path(case.get("outputs", {}).get(kind, "")).is_file() for kind in ("baseline", "final", "4k")):
            continue
        history = client.wait_generation(case["promptId"], lambda: None, lambda: None)
        save_json(destination / f"case{index}.history.json", history)
        outputs = {}
        for node_id, label in (("18", "baseline"), ("24", "final"), ("35", "4k")):
            path = destination / f"case{index}_{label}.png"
            # Strip graph metadata, retaining rendered pixels without enhancement.
            with Image.open(io.BytesIO(client.output(history, node_id))) as rendered:
                rendered.convert("RGB").save(path)
            outputs[label] = str(path)
        case["outputs"] = outputs
        case["status"] = "completed"
        save_json(manifest_path, manifest)
        print(json.dumps({"completed": case["label"], "outputs": outputs}), flush=True)
    board([(case["label"], Path(case["outputs"]["final"])) for case in manifest["cases"]], destination / "comparison.png")
    if args.face_box:
        items = [("Original reference photo", args.reference)] if args.reference else []
        for index, case in enumerate(manifest["cases"]):
            with Image.open(case["outputs"]["final"]) as original:
                width, height = original.size
            left, top, right, bottom = args.face_box
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError("Face crop must lie within both original images.")
            with Image.open(case["outputs"]["4k"]) as upscaled:
                sx, sy = upscaled.width / width, upscaled.height / height
                crop = upscaled.crop((round(left * sx), round(top * sy), round(right * sx), round(bottom * sy)))
                path = destination / f"case{index}_face.png"
                crop.save(path)
            items.append((case["label"] + " | 4K crop", path))
        board(items, destination / "comparison-faces.png")
    print(str(destination / "comparison.png"), flush=True)


if __name__ == "__main__":
    main()
