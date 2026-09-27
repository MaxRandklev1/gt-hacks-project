"""Acceptance run for the personal-base try-on: identical settings for every person, no manual fixes.

For each selfie: parse -> head crop -> personal base (Qwen, local ComfyUI) -> lock pose regions ->
composite each cached garment render at 1024 and 2K on the CPU. Saves results, masks, timing and
review boards under the ignored comfy-identity/personal-base-acceptance/<name>/.

python comfy-identity/run_personal_base_acceptance.py --name run1 --garments thread-2 thread-3 \
    --person jon=Jon/photo.png --person a=selfies/a.png
Run with the worker environment (services/worker/.venv) while the worker is idle.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw
import numpy as np

from services.worker.comfy import ComfyClient
from services.worker.catalog import normalized_png
from services.worker.compose import (alignment_score, base_regions, compose, covering_mask, edit_region, garment_mask,
                                     head_crop, lock_personal_base, resize_mask)
from services.worker.parsing import HumanParser, group_mask

GRAPH = ROOT / "comfy-identity/Qwen21_Personal_Base_2K.api.json"
BASE = ROOT / "ClothesSwap/Pose1_Weight3.png"
STYLED = ROOT / ".local/firebase-worker/styled"


def overlay(image, mask, color):
    image = image.convert("RGB").copy()
    tint = Image.new("RGB", image.size, color)
    return Image.composite(Image.blend(image, tint, 0.5), image, Image.fromarray((mask * 255).astype(np.uint8)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--person", action="append", required=True, help="label=path/to/selfie")
    parser.add_argument("--garments", nargs="+", required=True, help="Worker styled-cache keys or garment ids listed in --garment-map")
    parser.add_argument("--garment-map", type=Path, help="JSON {garment_id: styled_cache_key}")
    parser.add_argument("--base", type=Path, default=BASE,
                        help="Pose source image; use garment cache variants rendered on this same body (default: Pose1_Weight3.png).")
    parser.add_argument("--steps", type=int, default=24)
    parser.add_argument("--reuse", action="store_true", help="Re-composite saved raw personal bases without generating again.")
    args = parser.parse_args()
    out = ROOT / "comfy-identity/personal-base-acceptance" / args.name
    base_source = out / "pose_base_source.png"
    try:
        mapping = json.loads(args.garment_map.read_text()) if args.garment_map else {}
        if not isinstance(mapping, dict):
            raise ValueError("The garment map must be a JSON object of garment IDs to cache keys.")
        base_data, _ = normalized_png(args.base)
        base_key = hashlib.sha256(base_data).hexdigest()[:32]
        for garment in args.garments:
            key = mapping.get(garment, garment)
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
                raise ValueError(f"Garment {garment} needs a valid styled-cache key in --garment-map.")
            cached_base = (STYLED / key / "base-key.txt").read_text().strip()
            if cached_base != base_key:
                raise ValueError(f"Garment {garment} was rendered on a different body. Match --base to its cached pose.")
        if args.reuse and base_source.is_file() and normalized_png(base_source)[0] != base_data:
            raise ValueError("This acceptance run used a different pose. Choose a new --name instead of --reuse.")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    out.mkdir(parents=True, exist_ok=True)
    human, comfy = HumanParser(), ComfyClient()
    template = json.loads(GRAPH.read_text(encoding="utf-8"))

    # Pose base (same 1 MP resize the garment renders used) and its 2K upscale, made once.
    base_source.write_bytes(base_data)
    base_name = comfy.upload_image(base_source)
    report = {"steps": args.steps, "baseKey": base_key, "people": {}, "garments": {}}

    garments = {}
    state = {}

    def prepare_garments():
        regions = base_regions(state["base_labels"])
        for garment in args.garments:
            key = mapping.get(garment, garment)
            render_1024 = Image.open(STYLED / key / "styled-1024.png").convert("RGB")
            render_2k = Image.open(STYLED / key / "styled-2k.png").convert("RGB")
            labels = human.parse(render_1024)
            mask = garment_mask(labels, regions)
            tee = group_mask(state["base_labels"], "garment")
            skin = group_mask(labels, "face") | group_mask(labels, "arms")
            garments[garment] = {"r1024": render_1024, "r2k": render_2k, "mask": mask, "uncovered": tee & ~mask, "skin": skin}
            score = alignment_score(state["base_labels"], labels, garment_occlusion=mask)
            report["garments"][garment] = {"alignment": round(score, 3), "uncoveredPixels": int((tee & ~mask).sum())}
            overlay(render_1024, mask, (255, 0, 160)).save(out / f"garment_{garment}_mask.png")
            print("garment", garment, "alignment", round(score, 3), flush=True)

    def generate(label, crop_path):
        profile = comfy.create_profile("acceptance-" + label, [crop_path])
        graph = json.loads(json.dumps(template))
        graph["1"]["inputs"]["image"] = base_name
        graph["14"]["inputs"]["profile_id"] = profile["id"]
        graph["9"]["inputs"]["steps"] = args.steps
        graph["29"]["inputs"]["source"] = ["17", 0]  # First-pass decode; the texture pass is skipped for speed.
        if "base_2k" in state:
            for node in ("39", "40", "41", "42"):
                graph.pop(node)
        for node in ("24", "35", "42", "43"):
            if node in graph:
                graph[node]["inputs"]["filename_prefix"] = f"Acceptance/{args.name}/{label}_{node}"
        result = comfy.wait_generation(comfy.submit(graph, "acceptance"), lambda: None, lambda: None, interval=0.5)
        read = lambda node: Image.open(io.BytesIO(comfy.output(result, node))).convert("RGB")
        raw_1024, raw_2k = read("24"), read("35")
        raw_1024.save(out / f"{label}_raw_1024.png")
        raw_2k.save(out / f"{label}_raw_2k.png")
        if "base_2k" not in state:
            state["base_1024"], state["base_2k"] = read("43"), read("42")
            state["base_1024"].save(out / "pose_base_1024.png")
            state["base_2k"].save(out / "pose_base_2k.png")
            state["base_labels"] = human.parse(state["base_1024"])
            prepare_garments()
        return raw_1024, raw_2k

    for label, path in (item.split("=", 1) for item in args.person):
        timing = {}
        started = time.perf_counter()
        selfie = Image.open(path).convert("RGB")
        try:
            crop = head_crop(selfie, human.parse(selfie))
        except ValueError as error:
            report["people"][label] = {"rejected": str(error)}
            print(label, "rejected:", error, flush=True)
            continue
        crop_path = out / f"{label}_selfie_crop.png"
        crop.save(crop_path)
        timing["parse_crop"] = time.perf_counter() - started
        t = time.perf_counter()
        raw_path = out / f"{label}_raw_1024.png"
        if args.reuse and raw_path.is_file() and (out / "pose_base_1024.png").is_file():
            raw_1024, raw_2k = Image.open(raw_path).convert("RGB"), Image.open(out / f"{label}_raw_2k.png").convert("RGB")
            if "base_2k" not in state:
                state["base_1024"] = Image.open(out / "pose_base_1024.png").convert("RGB")
                state["base_2k"] = Image.open(out / "pose_base_2k.png").convert("RGB")
                state["base_labels"] = human.parse(state["base_1024"])
                prepare_garments()
        else:
            raw_1024, raw_2k = generate(label, crop_path)
        timing["generate"] = time.perf_counter() - t
        t = time.perf_counter()
        base_1024, base_2k, base_labels = state["base_1024"], state["base_2k"], state["base_labels"]
        result_labels = human.parse(raw_1024)
        edit = edit_region(base_labels, result_labels)
        personal_1024 = lock_personal_base(base_1024, raw_1024, edit)
        personal_2k = lock_personal_base(base_2k, raw_2k, edit)
        personal_labels = human.parse(personal_1024)
        hair = covering_mask(personal_labels)
        uncovered_personal = group_mask(personal_labels, "garment")
        base_plate = group_mask(base_labels, "garment") | (base_labels == 0)
        alignment = alignment_score(base_labels, personal_labels)
        timing["lock_and_masks"] = time.perf_counter() - t
        personal_1024.save(out / f"{label}_personal_1024.png")
        personal_2k.save(out / f"{label}_personal_2k.png")
        overlay(raw_1024, edit, (0, 160, 255)).save(out / f"{label}_edit_region.png")
        tiles = [crop.resize((int(512 * crop.width / crop.height), 512)), personal_1024.resize((512, 512))]
        for garment, data in garments.items():
            t = time.perf_counter()
            uncovered = data["uncovered"] | uncovered_personal
            result_1024 = compose(personal_1024, data["r1024"], data["mask"], hair, uncovered, base_1024, data["skin"], base_plate)
            size_2k = personal_2k.size
            result_2k = compose(personal_2k, data["r2k"], data["mask"], hair, uncovered, base_2k, data["skin"], base_plate)
            timing[f"compose_{garment}"] = time.perf_counter() - t
            result_1024.save(out / f"{label}_{garment}_1024.png")
            result_2k.save(out / f"{label}_{garment}_2k.jpg", quality=94)
            tiles.append(result_1024.resize((512, 512)))
            face = result_2k.crop((int(size_2k[0] * 0.3), 0, int(size_2k[0] * 0.7), int(size_2k[1] * 0.4))).resize((512, 512))
            tiles.append(face)
        board = Image.new("RGB", (sum(tile.width for tile in tiles), 512), "white")
        x = 0
        for tile in tiles:
            board.paste(tile, (x, 0))
            x += tile.width
        ImageDraw.Draw(board).text((8, 8), label, fill=(255, 0, 0))
        board.save(out / f"{label}_board.jpg", quality=90)
        timing["total"] = time.perf_counter() - started
        report["people"][label] = {"alignment": round(alignment, 3), "timing": {k: round(v, 2) for k, v in timing.items()}}
        print(label, json.dumps(report["people"][label]), flush=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
