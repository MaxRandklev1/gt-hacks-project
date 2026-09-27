"""Personal-base onboarding and CPU try-on compositing for the local worker.

Onboarding (GPU): one Qwen pass puts the person's whole head, hair/head covering and skin tone on the
fixed pose, with a neutral expression. Everything outside that edit region is locked to the pose
base, so the cached garment renders line up exactly.
Try-on (CPU only): garment region from the garment render, onto the personal base, with the
person's hair/covering matted back over it. No ComfyUI call, so scans never queue behind onboarding.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid

import numpy as np
from PIL import Image

try:
    from .comfy import patch_personal_graph, safe_id
    from .compose import (CompositeError, alignment_score, base_regions, compose, covering_mask, edit_region, garment_mask,
                          head_crop, lock_personal_base, resize_mask)
    from .parsing import HumanParser, group_mask
except ImportError:
    from comfy import patch_personal_graph, safe_id
    from compose import (CompositeError, alignment_score, base_regions, compose, covering_mask, edit_region, garment_mask,
                         head_crop, lock_personal_base, resize_mask)
    from parsing import HumanParser, group_mask


PERSONAL_STEPS = 24
MIN_ALIGNMENT = 0.85  # Each visible pose anchor for garments; original lower-body IoU for personal bases.
GARMENT_MASK_VERSION = 2  # Recompute masks/scores after the bounded collar/hem-occlusion update.
MAX_ASSET_BYTES = 40 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")


class PersonalProgress:
    """Observed stages and sampler counts, never a guessed total-completion percentage."""

    def __init__(self, notify, details=None):
        self.notify, self.details = notify, details
        self.fields, self.sent = {}, {}
        self.sent_at = 0.0
        self.upscale_nodes = frozenset()

    def observe_graph(self, graph):
        expected = {"34": "ImageUpscaleWithModel", "37": "SplitImageWithAlpha", "38": "ImageScaleBy", "35": "SaveImage"}
        self.upscale_nodes = {node for node, kind in expected.items() if graph.get(node, {}).get("class_type") == kind}

    def publish(self, force=False):
        stamp = time.monotonic()
        changed = self.fields != self.sent
        if not self.fields or not (force or (changed and stamp - self.sent_at >= 1) or stamp - self.sent_at >= 10):
            return
        if self.details is not None:
            self.details(dict(self.fields))
        else:
            self.notify(self.fields["message"], None)
        self.sent, self.sent_at = dict(self.fields), stamp

    def stage(self, stage, message):
        self.fields = {"stage": stage, "message": message, "progress": None, "sampling": None}
        self.publish(force=True)

    def event(self, event):
        if event.get("unavailable"):
            if self.fields.get("sampling") is not None:
                self.stage("creating_look", "Creating your look. Step updates are unavailable; the worker is still monitoring the image.")
            return
        node = event.get("node")
        if node == "9" and self.fields.get("stage") in ("upscaling_look", "fitting_look", "saving_look", "ready"):
            return  # Delayed transport events cannot move completed stages backward.
        if node == "9" and "step" in event:
            step, total = event["step"], event["total"]
            if type(step) is not int or type(total) is not int or not 0 <= step <= total <= 10000 or total <= 0:
                return
            previous = self.fields.get("sampling")
            if previous and (total != previous["total"] or step < previous["step"]):
                return
            self.fields = {"stage": "creating_look", "message": f"Creating your look: step {step} of {total}.",
                           "progress": None, "sampling": {"step": step, "total": total}}
            self.publish()
        elif node == "9":
            if self.fields.get("sampling") is None:
                self.stage("creating_look", "Creating your face, hair and skin tone on the selected body.")
        elif node in self.upscale_nodes:
            if self.fields.get("stage") != "upscaling_look":
                self.stage("upscaling_look", "Preparing the higher-resolution version of your look.")

    def heartbeat(self):
        self.publish()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def png_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def mask_png(mask):
    return png_bytes(Image.fromarray(mask.astype(np.uint8) * 255))


def read_mask(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert("L")) > 127


def personal_paths(uid, version, base_key):
    prefix = f"users/{safe_id(uid)}/identity/{safe_id(version)}/personal-{safe_id(base_key)}"
    return {"p1024": prefix + "-1024.png", "p2k": prefix + "-2k.png", "hair": prefix + "-hair.png", "tee": prefix + "-tee.png"}


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


class PersonalBaseMixin:
    """Mixed into Worker; relies on its store, comfy, state_dir and personal_template attributes."""

    _parser_lock = threading.Lock()

    def human(self):
        with self._parser_lock:
            if getattr(self, "_human", None) is None:
                self._human = HumanParser()
            return self._human

    # ---- pose base and garment masks ------------------------------------------------------------

    def pose_dir(self, base_key):
        return self.state_dir / "pose" / safe_id(base_key)

    def register_pose_source(self, base_png_bytes):
        """Content-addressed pose base shared by every garment that uses the same base image."""
        base_key = sha256(base_png_bytes)[:32]
        source = self.pose_dir(base_key) / "source.png"
        if not source.is_file():
            atomic_write(source, base_png_bytes)
        return base_key

    def pose_ready(self, base_key):
        folder = self.pose_dir(base_key)
        return all((folder / name).is_file() for name in ("base-1024.png", "base-2k.png"))

    def prepare_pose(self, base_key, guard):
        """Cache only the fixed pose resize/upscale; no identity profile, diffusion or face edit."""
        if self.pose_ready(base_key):
            return False
        guard()
        graph = {key: json.loads(json.dumps(self.personal_template[key]))
                 for key in ("1", "31", "33", "39", "40", "41", "42", "43")}
        expected = {"1": "LoadImage", "31": "ImageScaleToTotalPixels", "33": "UpscaleModelLoader",
                    "39": "SplitImageWithAlpha", "40": "ImageUpscaleWithModel", "41": "ImageScaleBy",
                    "42": "SaveImage", "43": "SaveImage"}
        if any(graph[key].get("class_type") != value for key, value in expected.items()):
            raise CompositeError("The configured pose preparation graph is invalid.")
        graph["1"]["inputs"]["image"] = self.comfy.upload_image(self.pose_dir(base_key) / "source.png")
        for node in ("42", "43"):
            graph[node]["inputs"]["filename_prefix"] = f"PoseBase/{safe_id(base_key)}/node{node}"
        prompt_id = self.comfy.submit(graph, "pose-" + safe_id(base_key))
        result = self.comfy.wait_generation(prompt_id, guard, lambda: None, timeout=300, interval=0.5)
        base = Image.open(io.BytesIO(self.comfy.output(result, "43"))).convert("RGB")
        upscale = Image.open(io.BytesIO(self.comfy.output(result, "42"))).convert("RGB")
        if upscale.size != (2 * base.width, 2 * base.height):
            raise CompositeError("The pose upscale returned unexpected dimensions.")
        guard()
        atomic_write(self.pose_dir(base_key) / "base-1024.png", png_bytes(base))
        atomic_write(self.pose_dir(base_key) / "base-2k.png", png_bytes(upscale))
        return True

    def pose(self, base_key):
        folder = self.pose_dir(base_key)
        cache = getattr(self, "_pose_cache", None)
        if cache is None:
            cache = self._pose_cache = {}
        if base_key not in cache:
            base_1024 = Image.open(folder / "base-1024.png").convert("RGB")
            base_2k = Image.open(folder / "base-2k.png").convert("RGB")
            labels = self.human().parse(base_1024)
            plate = group_mask(labels, "garment") | (labels == 0)  # Pose-base tee or background.
            cache[base_key] = {"b1024": base_1024, "b2k": base_2k, "labels": labels, "plate": plate}
        return cache[base_key]

    def garment_masks(self, styled_key, outputs, base_key):
        """Garment/uncovered/model-skin masks for a cached render, computed once on the CPU."""
        path = outputs[0].parent / f"masks-v{GARMENT_MASK_VERSION}-{base_key}.npz"
        if not path.is_file():
            pose = self.pose(base_key)
            labels = self.human().parse(Image.open(outputs[0]).convert("RGB"))
            garment = garment_mask(labels, base_regions(pose["labels"]))
            score = alignment_score(pose["labels"], labels, garment_occlusion=garment)
            tee = group_mask(pose["labels"], "garment")
            skin = group_mask(labels, "face") | group_mask(labels, "arms")
            buffer = io.BytesIO()
            np.savez_compressed(buffer, garment=garment, uncovered=tee & ~garment, skin=skin, alignment=np.float32(score))
            atomic_write(path, buffer.getvalue())
        data = np.load(path)
        if float(data["alignment"]) < MIN_ALIGNMENT:
            raise ValueError("This garment render does not line up with the pose base. Re-render it before use.")
        return {key: data[key] for key in ("garment", "uncovered", "skin")}

    # ---- onboarding -----------------------------------------------------------------------------

    def personal_base(self, uid, job_id, selfie_path, base_key, guard, notify, *, details=None):
        """Generate, lock and mask one personal base. Returns PNG bytes: 1024, 2K, hair/covering mask, tee mask."""
        progress = PersonalProgress(notify, details)
        progress.stage("preparing_reference", "Finding your face, hair and head covering in your selfie.")
        crop_path = Path(selfie_path).with_name("selfie-crop.png")
        selfie = Image.open(selfie_path).convert("RGB")
        head_crop(selfie, self.human().parse(selfie)).save(crop_path)
        guard()
        progress.stage("preparing_reference", "Preparing your selfie and the selected body reference.")
        profile = self.comfy.create_profile("personal-" + sha256(uid.encode())[:12] + "-" + job_id[:20], [crop_path])
        need_pose = not self.pose_ready(base_key)
        base_name = self.comfy.upload_image(self.pose_dir(base_key) / "source.png")
        graph = patch_personal_graph(self.personal_template, base_image=base_name, profile_id=safe_id(profile["id"]),
                                     job_id=job_id, steps=PERSONAL_STEPS, base_outputs=need_pose)
        progress.observe_graph(graph)
        progress.stage("creating_look", "Loading image models and processing your reference. Your look is starting.")
        guard()
        # Connect before the one non-retried submission. Missing/disconnected observation never resubmits work.
        with self.comfy.progress_events(job_id) as events:
            prompt_id = self.comfy.submit(graph, job_id)
            result = self.comfy.wait_generation(prompt_id, guard, progress.heartbeat, timeout=900, interval=0.5,
                                                events=events, on_event=progress.event)
        read = lambda node: Image.open(io.BytesIO(self.comfy.output(result, node))).convert("RGB")
        if need_pose:
            atomic_write(self.pose_dir(base_key) / "base-1024.png", png_bytes(read("43")))
            atomic_write(self.pose_dir(base_key) / "base-2k.png", png_bytes(read("42")))
        progress.stage("fitting_look", "Aligning your look and preparing the hair and clothing boundaries.")
        raw_1024, raw_2k = read("24"), read("35")
        pose = self.pose(base_key)
        edit = edit_region(pose["labels"], self.human().parse(raw_1024))
        personal_1024 = lock_personal_base(pose["b1024"], raw_1024, edit)
        personal_2k = lock_personal_base(pose["b2k"], raw_2k, edit)
        labels = self.human().parse(personal_1024)
        # A new face/beard shape is expected; the locked body must still match the pose.
        if alignment_score(pose["labels"], labels, include_face=False) < MIN_ALIGNMENT:
            raise CompositeError("Your look didn't line up with the pose. Please try another selfie.")
        if not group_mask(labels, "face").any():
            raise CompositeError("We couldn't create a clear face from this selfie. Take one facing the camera in good light.")
        return png_bytes(personal_1024), png_bytes(personal_2k), mask_png(covering_mask(labels)), mask_png(group_mask(labels, "garment"))

    def local_personal(self, uid, version, base_key):
        return self.state_dir / "personal" / safe_id(uid) / safe_id(version) / safe_id(base_key)

    def cache_personal(self, uid, version, base_key, assets):
        folder = self.local_personal(uid, version, base_key)
        for name, data in assets.items():
            atomic_write(folder / f"{name}.png", data)

    def personal_assets(self, uid, identity, base_key):
        """Verified personal base for this pose: local cache, else the owner's Storage copy."""
        version = safe_id(identity.get("version"))
        record = (identity.get("personalBases") or {}).get(base_key)
        if not isinstance(record, dict):
            raise CompositeError("This piece uses a different pose. Retake your selfie to try it on.")
        expected = personal_paths(uid, version, base_key)
        if any(record.get(name) != path for name, path in expected.items()):
            raise CompositeError("The saved look does not match this account. Retake your selfie.")
        folder = self.local_personal(uid, version, base_key)
        assets = {}
        for name, path in expected.items():
            digest = record.get(name + "Sha256")
            if not isinstance(digest, str) or not HEX.fullmatch(digest):
                raise CompositeError("The saved look is incomplete. Retake your selfie.")
            local = folder / f"{name}.png"
            data = local.read_bytes() if local.is_file() else None
            if data is None or sha256(data) != digest:
                data = self.store.download(path, MAX_ASSET_BYTES)
                if sha256(data) != digest:
                    raise CompositeError("Your saved look failed integrity validation. Retake your selfie.")
                atomic_write(local, data)
            assets[name] = data
        return {"p1024": Image.open(io.BytesIO(assets["p1024"])).convert("RGB"),
                "p2k": Image.open(io.BytesIO(assets["p2k"])).convert("RGB"),
                "hair": read_mask(assets["hair"]), "tee": read_mask(assets["tee"])}

    # ---- try-on ---------------------------------------------------------------------------------

    def composite_tryon(self, personal, render_paths, masks, base_key):
        pose = self.pose(base_key)
        r1024 = Image.open(render_paths[0]).convert("RGB")
        r2k = Image.open(render_paths[1]).convert("RGB")
        uncovered = masks["uncovered"] | personal["tee"]  # Tee left visible on either base.
        image = compose(personal["p1024"], r1024, masks["garment"], personal["hair"], uncovered, pose["b1024"], masks["skin"], pose["plate"])
        size = personal["p2k"].size
        image2k = compose(personal["p2k"], r2k, masks["garment"], personal["hair"], uncovered, pose["b2k"], masks["skin"], pose["plate"])
        return image, image2k


def isoformat(value):
    return value.isoformat() if isinstance(value, datetime) else value
