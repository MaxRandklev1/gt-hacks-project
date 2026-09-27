"""Likeness lab: upload a selfie, compare blind-labelled versions of yourself, pick the one that looks like you.

Runs only on this PC (http://127.0.0.1:8765) against the local ComfyUI. Uploaded photos and results stay
under the ignored comfy-identity/likeness-lab-sessions/. Nothing is sent to Firebase.

Versions (labels are shuffled per session and revealed after you pick):
  A  today's onboarding: selfie reference 0.35 MP, head-swap LoRA 0.65, 24 steps
  B  shortcuts undone: selfie reference 1 MP, 40 steps (head-swap LoRA stays 0.65; 1.0 invented long hair)
  C  B + close-up face pass: head crop enlarged to ~1024 px, redrawn from the selfie, blended back
  D  C + local HyperSwap identity polish on the close-up
  E  (only with extra selfies) C using every uploaded angle as a reference

Start with the worker environment while the GPU is otherwise idle:
  services/worker/.venv/Scripts/python.exe comfy-identity/likeness_lab/lab.py
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import random
import re
import sys
import threading
import time
import traceback
import uuid
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from PIL import Image, ImageOps

from services.worker.comfy import ComfyClient
from services.worker.compose import (covering_mask, edit_region, face_box, blend_face, head_crop, head_mask,
                                     lock_personal_base)
from services.worker.parsing import group_mask
from services.worker.worker import Worker

STATE = ROOT / ".local/firebase-worker"
SESSIONS = ROOT / "comfy-identity/likeness-lab-sessions"
PAGE = Path(__file__).with_name("index.html")
BODY_GRAPH = json.loads((ROOT / "comfy-identity/Qwen21_Personal_Base_2K.api.json").read_text(encoding="utf-8"))
FACE_GRAPH = json.loads((ROOT / "comfy-identity/Qwen21_Face_Pass_1024.api.json").read_text(encoding="utf-8"))
EXTRA_ANGLES = ("<image3> and <image4> show the same person as <image2> from other angles. Use all of them together "
                "for the person's identity; keep the head position of <image1>.\n")
DESCRIPTIONS = {
    "A": "Today's onboarding (small selfie reference, head-swap 0.65, 24 steps)",
    "B": "Shortcuts undone (full selfie reference, 40 steps)",
    "C": "B + close-up face pass (head redrawn at ~1024 px)",
    "D": "C + HyperSwap identity polish",
    "E": "C using all uploaded selfie angles",
}

worker = Worker(Mock(), ComfyClient(), Mock(), {}, STATE)
comfy = worker.comfy
gpu = threading.Lock()  # One comparison at a time; ComfyUI queues work serially anyway.
sessions = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def garments():
    items = []
    for folder in sorted((STATE / "styled").glob("*")):
        key_file = folder / "base-key.txt"
        if key_file.is_file() and (folder / "styled-1024.png").is_file() and worker.pose_ready(key_file.read_text().strip()):
            items.append({"key": folder.name, "base": key_file.read_text().strip()})
    return items


def save(session, name, image, quality=None):
    path = SESSIONS / session["id"] / name
    if quality:
        image.save(path, quality=quality)
    else:
        image.save(path)
    return name


def run_graph(graph, outputs):
    result = comfy.wait_generation(comfy.submit(graph, "likeness-lab"), lambda: None, lambda: None, timeout=1200, interval=0.5)
    return [Image.open(io.BytesIO(comfy.output(result, node))).convert("RGB") for node in outputs]


def add_extras(graph, extra_names, image_node="8", prompt_node="15"):
    for index, name in enumerate(extra_names[:2]):
        node_id = f"x{index}"
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph[image_node]["inputs"][f"images.image_{index + 3}"] = [node_id, 0]
    graph[prompt_node]["inputs"]["edit_instructions"] = EXTRA_ANGLES + graph[prompt_node]["inputs"]["edit_instructions"]


def body_pass(base_key, profile_id, *, steps, reference_mp, lora, extra_names=()):
    graph = json.loads(json.dumps(BODY_GRAPH))
    graph["1"]["inputs"]["image"] = comfy.upload_image(worker.pose_dir(base_key) / "source.png")
    graph["14"]["inputs"].update(profile_id=profile_id, reference_index=0, use_trained_identity=False)
    graph["9"]["inputs"]["steps"] = steps
    graph["44"]["inputs"]["megapixels"] = reference_mp
    graph["4"]["inputs"]["strength_model"] = lora
    graph["29"]["inputs"]["source"] = ["17", 0]
    for node in ("39", "40", "41", "42", "43"):
        graph.pop(node, None)
    if extra_names:
        add_extras(graph, extra_names)
    raw_1024, raw_2k = run_graph(graph, ("24", "35"))
    pose = worker.pose(base_key)
    edit = edit_region(pose["labels"], worker.human().parse(raw_1024))
    return lock_personal_base(pose["b1024"], raw_1024, edit), lock_personal_base(pose["b2k"], raw_2k, edit)


def face_pass(personal_1024, personal_2k, profile_id, extra_names=()):
    """Redraw the head at ~1024 px and blend it back into both sizes. Returns (redrawn, polished) pairs."""
    labels = worker.human().parse(personal_1024)
    box = face_box(labels)
    crop = personal_2k.crop(tuple(2 * v for v in box)).resize((1024, 1024), Image.LANCZOS)
    crop_path = SESSIONS / "_crop.png"
    crop.save(crop_path)
    graph = json.loads(json.dumps(FACE_GRAPH))
    graph["1"]["inputs"]["image"] = comfy.upload_image(crop_path)
    graph["14"]["inputs"].update(profile_id=profile_id, reference_index=0, use_trained_identity=False)
    if extra_names:
        add_extras(graph, extra_names)
    redrawn, polished = run_graph(graph, ("24", "26"))
    original_head = head_mask(worker.human().parse(crop))
    results = []
    for version in (redrawn, polished):
        mask = original_head | head_mask(worker.human().parse(version))
        box2k = tuple(2 * v for v in box)
        results.append((blend_face(personal_1024, version, box, mask), blend_face(personal_2k, version, box2k, mask)))
    return results


def try_on(personal_1024, personal_2k, garment):
    labels = worker.human().parse(personal_1024)
    personal = {"p1024": personal_1024, "p2k": personal_2k, "hair": covering_mask(labels), "tee": group_mask(labels, "garment")}
    folder = STATE / "styled" / garment["key"]
    renders = (folder / "styled-1024.png", folder / "styled-2k.png")
    masks = worker.garment_masks(garment["key"], renders, garment["base"])
    return worker.composite_tryon(personal, renders, masks, garment["base"])


def face_view(image_2k, labels_1024):
    x0, y0, x1, y1 = face_box(labels_1024, margin=0.6)
    return image_2k.crop((2 * x0, 2 * y0, 2 * x1, 2 * y1)).resize((640, 640), Image.LANCZOS)


def run_session(session, selfie_bytes, extra_bytes, garment):
    folder = SESSIONS / session["id"]
    def progress(message):
        session["message"] = message
    try:
        with gpu:
            images = [ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB") for data in [selfie_bytes, *extra_bytes]]
            crops = []
            for index, image in enumerate(images):
                try:
                    crops.append(head_crop(image, worker.human().parse(image)))
                except ValueError as error:
                    raise ValueError(("Selfie" if index == 0 else f"Extra photo {index}") + ": " + str(error)) from None
            images[0].save(folder / "selfie.jpg", quality=92)
            crop_paths = []
            for index, crop in enumerate(crops):
                path = folder / f"crop-{index}.png"
                crop.save(path)
                crop_paths.append(path)
            profile_id = comfy.create_profile("likeness-lab-" + session["id"][:8], [crop_paths[0]])["id"]
            extra_names = [comfy.upload_image(path) for path in crop_paths[1:]]
            versions, timings = {}, {}

            progress("Version A: today's onboarding…")
            t = time.perf_counter()
            versions["A"] = body_pass(garment["base"], profile_id, steps=24, reference_mp=0.35, lora=0.65)
            timings["A"] = time.perf_counter() - t

            progress("Version B: full-detail selfie reference…")
            t = time.perf_counter()
            versions["B"] = body_pass(garment["base"], profile_id, steps=40, reference_mp=1.0, lora=0.65)
            timings["B"] = time.perf_counter() - t

            progress("Versions C and D: close-up face pass…")
            t = time.perf_counter()
            versions["C"], versions["D"] = face_pass(*versions["B"], profile_id)
            face_seconds = time.perf_counter() - t
            timings["C"] = timings["D"] = timings["B"] + face_seconds

            if extra_names:
                progress("Version E: all selfie angles…")
                t = time.perf_counter()
                body = body_pass(garment["base"], profile_id, steps=40, reference_mp=1.0, lora=0.65, extra_names=extra_names)
                versions["E"] = face_pass(*body, profile_id, extra_names)[0]
                timings["E"] = time.perf_counter() - t

            progress("Putting on the garment…")
            letters = list(versions)
            shuffled = random.sample(letters, len(letters))
            cards = []
            for label, variant in zip("12345", shuffled):
                p1024, p2k = versions[variant]
                image, image2k = try_on(p1024, p2k, garment)
                labels = worker.human().parse(p1024)
                cards.append({"label": label, "full": save(session, f"v{label}.png", image),
                              "face": save(session, f"v{label}-face.jpg", face_view(image2k, labels), 92)})
                session.setdefault("_mapping", {})[label] = variant
            session["_timings"] = {k: round(v, 1) for k, v in timings.items()}
            session.update(status="ready", message="Pick the one that looks most like you.", cards=cards)
            (folder / "session.json").write_text(json.dumps(public(session, reveal=True), indent=2))
    except Exception as error:
        traceback.print_exc()
        session.update(status="failed", message=str(error) if isinstance(error, ValueError) else f"Something failed: {type(error).__name__}. See the lab console.")


def public(session, reveal=False):
    data = {key: value for key, value in session.items() if not key.startswith("_")}
    if reveal or session.get("pick"):
        data["reveal"] = {label: {"version": variant, "description": DESCRIPTIONS[variant],
                                  "onboardingSeconds": session["_timings"].get(variant)}
                          for label, variant in session.get("_mapping", {}).items()}
    return data


def decode_image(data_url, limit=25 * 1024 * 1024):
    match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not match:
        raise ValueError("Use a JPG, PNG or WebP photo.")
    data = base64.b64decode(match[2])
    if not 0 < len(data) <= limit:
        raise ValueError("Each photo must be under 25 MB.")
    return data


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/":
            return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/garments":
            return self.send(200, garments())
        match = re.fullmatch(r"/api/garment/([0-9a-f]{32})\.jpg", self.path)
        if match:
            image = Image.open(STATE / "styled" / match[1] / "styled-1024.png").convert("RGB").resize((256, 256))
            buffer = io.BytesIO(); image.save(buffer, "JPEG", quality=85)
            return self.send(200, buffer.getvalue(), "image/jpeg")
        match = re.fullmatch(r"/api/session/([0-9a-f]{32})", self.path)
        if match and match[1] in sessions:
            return self.send(200, public(sessions[match[1]]))
        match = re.fullmatch(r"/files/([0-9a-f]{32})/([A-Za-z0-9_.-]+\.(?:png|jpg))", self.path)
        if match and (SESSIONS / match[1] / match[2]).is_file():
            path = SESSIONS / match[1] / match[2]
            return self.send(200, path.read_bytes(), "image/png" if path.suffix == ".png" else "image/jpeg")
        self.send(404, {"error": "Not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 120 * 1024 * 1024:
                raise ValueError("Upload is too large.")
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/run":
                if gpu.locked():
                    raise ValueError("A comparison is already running. Wait for it to finish.")
                garment = next((g for g in garments() if g["key"] == body.get("garment")), None)
                if not garment:
                    raise ValueError("Choose a garment.")
                selfie = decode_image(body.get("selfie"))
                extras = [decode_image(item) for item in (body.get("extras") or [])[:2]]
                session = {"id": uuid.uuid4().hex, "status": "running", "message": "Checking your photos…",
                           "createdAt": now(), "extras": len(extras)}
                (SESSIONS / session["id"]).mkdir(parents=True)
                sessions[session["id"]] = session
                threading.Thread(target=run_session, args=(session, selfie, extras, garment), daemon=True).start()
                return self.send(200, {"id": session["id"]})
            match = re.fullmatch(r"/api/session/([0-9a-f]{32})/pick", self.path)
            if match and match[1] in sessions:
                session = sessions[match[1]]
                label = body.get("label")
                if session.get("status") != "ready" or (label != "none" and label not in session.get("_mapping", {})):
                    raise ValueError("Pick one of the versions, or 'none of these'.")
                session["pick"] = {"label": label, "version": session["_mapping"].get(label, "none"),
                                   "whatsOff": [str(item)[:40] for item in body.get("whatsOff", [])][:8],
                                   "note": str(body.get("note", ""))[:1000], "at": now()}
                folder = SESSIONS / session["id"]
                (folder / "session.json").write_text(json.dumps(public(session, reveal=True), indent=2))
                with (SESSIONS / "picks.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps({"session": session["id"], **session["pick"], "timings": session["_timings"]}) + "\n")
                return self.send(200, public(session))
            self.send(404, {"error": "Not found"})
        except ValueError as error:
            self.send(400, {"error": str(error)})


if __name__ == "__main__":
    SESSIONS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Likeness lab: http://127.0.0.1:8765  (Ctrl+C to stop)", flush=True)
    server.serve_forever()
