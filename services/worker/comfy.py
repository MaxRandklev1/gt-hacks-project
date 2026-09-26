"""Bounded, loopback-only client for the existing local ComfyUI installation."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import json
import hashlib
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

import requests


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class ComfyError(RuntimeError):
    pass


def safe_id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid local identifier.")
    return value


def contained(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Local file escaped the configured profile directory.")
    return path


def patch_graph(template, *, profile_id, base_image, garment_image, job_id):
    """Patch data inputs only; never accept a graph, prompt, model or path from a job."""
    safe_id(profile_id)
    safe_id(job_id)
    graph = copy.deepcopy(template)
    required = {"1": "LoadImage", "14": "UniversalIdentityProfile", "32": "LoadImage",
                "24": "SaveImage", "35": "SaveImage"}
    for node_id, expected in required.items():
        if graph.get(node_id, {}).get("class_type") != expected:
            raise ValueError("Configured workflow does not match the reviewed try-on graph.")
    for filename in (base_image, garment_image):
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", filename):
            raise ValueError("Expected a worker-generated Comfy input filename.")
    graph["1"]["inputs"]["image"] = base_image
    graph["32"]["inputs"]["image"] = garment_image
    graph["14"]["inputs"].update(profile_id=profile_id, reference_index=0, use_trained_identity=True)
    for node_id, node in graph.items():
        if node["class_type"] == "SaveImage":
            node["inputs"]["filename_prefix"] = f"CloudTryOn/{job_id}/node{node_id}"
    return graph


class ComfyClient:
    def __init__(self, base_url="http://127.0.0.1:8188", session=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username
                or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("ComfyUI must use an http://127.0.0.1 loopback URL.")
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.trust_env = False  # Never route private photos through a proxy.
        self.session.headers.update({"X-Universal-Identity": "1"})

    def request(self, method, route, **kwargs):
        response = self.session.request(method, self.base_url + route, timeout=(10, 60),
                                        allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise ComfyError(f"Local ComfyUI rejected {method} {route.split('?')[0]} ({response.status_code}).")
        return response

    def json(self, method, route, **kwargs):
        return self.request(method, route, **kwargs).json()

    def idle(self):
        status = self.json("GET", "/universal-identity/status")
        queue = self.json("GET", "/queue")
        return not status.get("active") and not queue.get("queue_running") and not queue.get("queue_pending")

    def create_profile(self, name, photos):
        with ExitStack() as stack:
            files = [("files", (f"photo-{index}.png", stack.enter_context(Path(path).open("rb")), "image/png"))
                     for index, path in enumerate(photos)]
            return self.json("POST", "/universal-identity/profiles", data={"name": name}, files=files)["profile"]

    def profile(self, profile_id):
        return self.json("GET", f"/universal-identity/profiles/{safe_id(profile_id)}")["profile"]

    def train(self, profile_id):
        return self.json("POST", f"/universal-identity/profiles/{safe_id(profile_id)}/train", json={"steps": 400})["job"]

    def wait_training(self, training_id, guard, progress, *, timeout=3600, interval=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            guard()
            job = self.json("GET", f"/universal-identity/jobs/{safe_id(training_id)}")["job"]
            if job["status"] == "failed":
                raise ComfyError("Local identity training failed. Check the local training log.")
            if job["status"] == "completed":
                return job
            progress(min(0.95, max(0, job.get("current_step", 0)) / 400))
            time.sleep(interval)
        raise ComfyError("Local training exceeded its time limit; inspect ComfyUI before retrying.")

    def photo(self, profile_id, photo_id):
        return self.request("GET", f"/universal-identity/profiles/{safe_id(profile_id)}/photos/{safe_id(photo_id)}").content

    def upload_image(self, path):
        filename = "cloud_" + uuid.uuid4().hex + ".png"
        with Path(path).open("rb") as handle:
            result = self.json("POST", "/upload/image", files={"image": (filename, handle, "image/png")},
                               data={"type": "input", "overwrite": "false"})
        if result.get("name") != filename or result.get("subfolder", ""):
            raise ComfyError("Unexpected local upload response.")
        return filename

    def submit(self, graph, job_id):
        # Do not retry this POST: an ambiguous response can still mean GPU work was queued.
        result = self.json("POST", "/prompt", json={"prompt": graph, "client_id": safe_id(job_id),
                                                     "extra_data": {"firebase_job_id": job_id}})
        return result["prompt_id"]

    def wait_generation(self, prompt_id, guard, progress, *, timeout=3600, interval=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            guard()
            item = self.json("GET", f"/history/{safe_id(prompt_id)}").get(prompt_id)
            if item:
                if item.get("status", {}).get("status_str") != "success":
                    raise ComfyError("Local image generation failed. Check ComfyUI's execution log.")
                return item
            progress()
            time.sleep(interval)
        raise ComfyError("Local generation exceeded its time limit; inspect ComfyUI before retrying.")

    def output(self, history, node_id):
        images = history.get("outputs", {}).get(str(node_id), {}).get("images", [])
        if len(images) != 1:
            raise ComfyError("The workflow did not return exactly one expected output image.")
        image = images[0]
        if image.get("type") != "output":
            raise ComfyError("Unexpected ComfyUI output type.")
        return self.request("GET", "/view", params={key: image[key] for key in ("filename", "subfolder", "type")}).content


class LocalProfiles:
    """Only access profiles created by this worker; never load an arbitrary personal profile."""
    def __init__(self, root):
        self.root = Path(root).resolve()

    def read(self, profile_id):
        directory = contained(self.root, safe_id(profile_id))
        profile = json.loads((directory / "profile.json").read_text(encoding="utf-8"))
        if profile.get("id") != profile_id:
            raise ValueError("Local profile manifest mismatch.")
        return directory, profile

    def save(self, directory, profile):
        path = directory / "profile.json"
        temporary = directory / (".cloud-" + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        temporary.replace(path)

    def mark_owner(self, profile_id, uid, version, *, require_selfie=False):
        directory, profile = self.read(profile_id)
        profile["cloud_identity"] = {"uid": uid, "version": version}
        if require_selfie:
            profile["inference_reference_required"] = True
        self.save(directory, profile)

    def bind_reference(self, profile_id, uid, version, data, *, source="live_selfie"):
        if source not in ("live_selfie", "recent_selfie"):
            raise ValueError("Invalid selfie reference source.")
        directory, profile = self.read(profile_id)
        if profile.get("cloud_identity") != {"uid": uid, "version": version}:
            raise ValueError("Reference owner does not match the local profile.")
        path = contained(directory, "cloud-reference/reference.png")
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_name(".reference-" + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        profile["inference_reference_required"] = True
        profile["inference_reference"] = {"source": source, "filename": "cloud-reference/reference.png",
                                          "sha256": hashlib.sha256(data).hexdigest(),
                                          "owner": {"uid": uid, "version": version}}
        self.save(directory, profile)

    def verify_reference(self, profile_id, uid, version, expected_hash, *, source="live_selfie"):
        directory, profile = self.read(profile_id)
        reference = profile.get("inference_reference") or {}
        owner = {"uid": uid, "version": version}
        if (profile.get("cloud_identity") != owner or profile.get("inference_reference_required") is not True
                or source not in ("live_selfie", "recent_selfie") or reference.get("source") != source or reference.get("owner") != owner
                or reference.get("filename") != "cloud-reference/reference.png" or reference.get("sha256") != expected_hash):
            raise ValueError("The cached profile is missing its required selfie reference.")
        path = contained(directory, reference["filename"])
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("The cached selfie reference failed integrity validation.")
        return path

    def adapter(self, profile_id, uid, version):
        directory, profile = self.read(profile_id)
        if profile.get("cloud_identity") != {"uid": uid, "version": version}:
            raise ValueError("Local profile does not belong to this cloud identity.")
        relative = (profile.get("latest_successful") or {}).get("adapter_relative")
        if not isinstance(relative, str):
            raise ValueError("Local identity has no successful adapter.")
        adapter = contained(directory, relative)
        if not adapter.is_file() or adapter.suffix != ".safetensors":
            raise ValueError("Local trained adapter is missing.")
        return adapter, profile

    def restore(self, profile_id, uid, manifest, adapter_bytes, reference_bytes=None):
        directory, profile = self.read(profile_id)
        trigger = manifest.get("trigger")
        if not isinstance(trigger, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", trigger):
            raise ValueError("Invalid identity trigger in saved manifest.")
        selfie = manifest.get("schemaVersion") == 2
        if selfie and (manifest.get("referenceSource") not in ("live_selfie", "recent_selfie") or reference_bytes is None
                       or hashlib.sha256(reference_bytes).hexdigest() != manifest.get("referenceSha256")):
            raise ValueError("Restoring this identity requires its verified selfie reference.")
        adapter = contained(directory, "cloud-import/adapter.safetensors")
        adapter.parent.mkdir(exist_ok=True)
        adapter.write_bytes(adapter_bytes)
        profile["trigger"] = trigger
        for photo in profile["photos"]:
            photo["caption"] = f"photo of {trigger}, a person"
            if selfie:
                photo["selected"] = False  # The restore reference is not a training dataset.
        profile["cloud_identity"] = {"uid": uid, "version": manifest["version"]}
        profile["latest_successful"] = {"adapter_relative": "cloud-import/adapter.safetensors", "steps": 400}
        profile["training"] = {"status": "completed", "job_id": None}
        self.save(directory, profile)
        if selfie:
            self.bind_reference(profile_id, uid, manifest["version"], reference_bytes, source=manifest["referenceSource"])
