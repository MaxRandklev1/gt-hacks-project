"""Local-only identity profiles and isolated training. No ComfyUI ML imports."""
from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from PIL import Image, ImageOps

BASE_ROUTE = "/universal-identity"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$")
ACTIVE_STATES = {"preparing", "running"}


class ProfileError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProfileError("Invalid profile, photo, or job identifier.")
    return value


def within(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ProfileError("Path must remain inside its local profile.")
    return path


def selected_photos(profile):
    return [photo for photo in profile["photos"] if photo.get("selected", True)]


def data_warnings(profile):
    photos = selected_photos(profile)
    warnings = []
    if len(photos) < 3:
        warnings.append("Fewer than three selected photos: training can run, but varied clear photos usually help identity learning.")
    if any(min(p["width"], p["height"]) < 256 for p in photos):
        warnings.append("Some selected photos are small; limited face detail may reduce likeness. They remain selected.")
    if len({p.get("sha256", p["id"]) for p in photos}) < len(photos):
        warnings.append("Some selected images are duplicates. They remain selected.")
    return warnings


class ProfileStore:
    def __init__(self, config_path=None, config=None):
        self.config_path = Path(config_path or Path(__file__).with_name("config.json"))
        self._config = config
        self.mutex = threading.RLock()

    @property
    def config(self):
        value = self._config if self._config is not None else read_json(self.config_path)
        training = Path(value["training_root"]).resolve()
        profiles = Path(value["profiles_root"]).resolve()
        if not training.is_absolute() or not profiles.is_relative_to(training) or profiles == training:
            raise ProfileError("Configured profiles_root must be inside the local training_root.")
        return {"training_root": training, "profiles_root": profiles}

    @property
    def root(self):
        root = self.config["profiles_root"]
        root.mkdir(parents=True, exist_ok=True)
        return root

    def profile_dir(self, profile_id):
        return within(self.root, identifier(profile_id))

    def load(self, profile_id):
        path = self.profile_dir(profile_id) / "profile.json"
        if not path.is_file():
            raise ProfileError("Profile not found. Upload a folder or select an existing profile.")
        data = read_json(path)
        if data.get("id") != profile_id:
            raise ProfileError("Profile manifest identity does not match its directory.")
        return data

    def save(self, profile):
        write_json(self.profile_dir(profile["id"]) / "profile.json", profile)

    def list_profiles(self):
        result = []
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or not IDENTIFIER.fullmatch(folder.name):
                continue
            try:
                result.append(self.public(self.load(folder.name)))
            except (ValueError, OSError, KeyError):
                continue
        return result

    def adapter_path(self, profile):
        success = profile.get("latest_successful") or {}
        relative = success.get("adapter_relative")
        if not relative:
            return None
        path = within(self.profile_dir(profile["id"]), relative)
        return path if path.is_file() and path.suffix == ".safetensors" else None

    def public(self, profile):
        result = {key: profile[key] for key in ("id", "name", "trigger", "created_at")}
        result["photos"] = [
            {key: photo[key] for key in ("id", "name", "selected", "width", "height")}
            | {"thumbnail_url": f"{BASE_ROUTE}/profiles/{profile['id']}/photos/{photo['id']}?thumbnail=1"}
            for photo in profile["photos"]
        ]
        result["photo_count"] = len(profile["photos"])
        result["selected_count"] = len(selected_photos(profile))
        result["reference_source"] = "live_selfie" if profile.get("inference_reference_required") else "selected_photo"
        result["warnings"] = data_warnings(profile)
        result["training"] = {
            "status": profile.get("training", {}).get("status", "not_trained"),
            "adapter_available": self.adapter_path(profile) is not None,
            "steps": (profile.get("latest_successful") or {}).get("steps"),
            "job_id": profile.get("training", {}).get("job_id"),
        }
        return result

    def create(self, name, uploads):
        name = str(name).strip()[:80] or "Identity profile"
        if not uploads:
            raise ProfileError("Upload at least one image.")
        total = sum(Path(path).stat().st_size for _, path in uploads)
        if total > MAX_TOTAL_BYTES:
            raise ProfileError("Folder upload exceeds 200 MiB.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "person"
        unique = uuid.uuid4().hex[:12]
        profile_id = f"{slug}-{unique}"
        directory = self.profile_dir(profile_id)
        directory.mkdir(exist_ok=False)
        try:
            (directory / "photos").mkdir()
            (directory / "originals").mkdir()
            profile = {"version": 1, "id": profile_id, "name": name,
                       "trigger": f"u{unique}_person", "created_at": time.time(),
                       "photos": [], "training": {"status": "not_trained"}, "latest_successful": None}
            for number, (original_name, source) in enumerate(uploads):
                source = Path(source)
                suffix = Path(original_name).suffix.lower()
                if suffix not in IMAGE_SUFFIXES or source.stat().st_size > MAX_FILE_BYTES:
                    raise ProfileError(f"Unsupported image or file exceeds 20 MiB: {original_name}")
                photo_id = f"p{number:04d}"
                with Image.open(source) as decoded:
                    if decoded.width * decoded.height > 40_000_000:
                        raise ProfileError(f"Image exceeds 40 megapixels: {original_name}")
                    image = ImageOps.exif_transpose(decoded).convert("RGB")
                    image.load()
                photo_path = directory / "photos" / f"{photo_id}.png"
                image.save(photo_path)
                shutil.copyfile(source, directory / "originals" / f"{photo_id}{suffix}")
                clean_name = str(original_name).replace("\\", "/").rsplit("/", 1)[-1][:160]
                profile["photos"].append({"id": photo_id, "name": clean_name,
                    "filename": f"photos/{photo_id}.png", "selected": True,
                    "width": image.width, "height": image.height,
                    "sha256": hashlib.sha256(photo_path.read_bytes()).hexdigest(),
                    "caption": f"photo of {profile['trigger']}, a person"})
            self.save(profile)
            return profile
        except Exception:
            # Only the newly-created UUID directory can reach this cleanup.
            shutil.rmtree(directory)
            raise

    def update(self, profile_id, changes):
        with self.mutex:
            profile = self.load(profile_id)
            if "name" in changes:
                name = str(changes["name"]).strip()[:80]
                if not name:
                    raise ProfileError("Profile name cannot be empty.")
                profile["name"] = name
            if "selected_photo_ids" in changes:
                ids = changes["selected_photo_ids"]
                valid = {p["id"] for p in profile["photos"]}
                if not isinstance(ids, list) or not ids or any(not isinstance(p, str) or p not in valid for p in ids):
                    raise ProfileError("Select at least one photo belonging to this profile.")
                wanted = set(ids)
                for photo in profile["photos"]:
                    photo["selected"] = photo["id"] in wanted
            self.save(profile)
            return profile

    def photo_path(self, profile, photo_id):
        identifier(photo_id)
        photo = next((p for p in profile["photos"] if p["id"] == photo_id), None)
        if photo is None:
            raise ProfileError("Photo not found in this profile.")
        return within(self.profile_dir(profile["id"]), photo["filename"])

    def reference_path(self, profile, reference_index):
        reference = profile.get("inference_reference")
        if profile.get("inference_reference_required") or reference is not None:
            if (not isinstance(reference, dict) or reference.get("source") != "live_selfie"
                    or reference.get("owner") != profile.get("cloud_identity")
                    or not isinstance(reference.get("owner"), dict)
                    or reference.get("filename") != "cloud-reference/reference.png"):
                raise ProfileError("This cloud identity requires its live-selfie reference. Restore it through the app.")
            path = within(self.profile_dir(profile["id"]), reference["filename"])
            if (not path.is_file() or path.stat().st_size > MAX_FILE_BYTES
                    or hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256")):
                raise ProfileError("The live-selfie reference is missing or changed. Restore it through the app.")
            return path
        photos = selected_photos(profile)
        if not 0 <= reference_index < len(photos):
            raise ProfileError("Reference index must identify one of the selected photos (starting at 0).")
        return self.photo_path(profile, photos[reference_index]["id"])


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of death.
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class TrainingManager:
    def __init__(self, store):
        self.store = store
        self.latest_job_path = None
        self.lock = threading.RLock()

    @property
    def lock_path(self):
        return self.store.root / ".training.lock"

    def active_job(self):
        with self.lock:
            if not self.lock_path.exists():
                return None
            lock = read_json(self.lock_path)
            job_path = within(self.store.root, lock["job_relative"])
            job = read_json(job_path)
            worker_alive = process_alive(job.get("pid")) or process_alive(job.get("launcher_pid"))
            if job["status"] not in ACTIVE_STATES:
                if worker_alive:
                    # CUDA teardown belongs to the process, not just the last
                    # Python training statement. Reserve until it has exited.
                    job["status"] = "running"
                    job["finalizing"] = True
                    return job
                self.lock_path.unlink(missing_ok=True)
                return None
            # A short no-PID interval occurs between reservation and Popen.
            alive = worker_alive if job.get("pid") else time.time() - job["created_at"] < 60
            if alive:
                return job
            job.update(status="failed", error="Training process stopped before reporting completion.")
            write_json(job_path, job)
            profile = self.store.load(job["profile_id"])
            profile["training"] = {"status": "failed", "job_id": job["job_id"]}
            self.store.save(profile)
            self.lock_path.unlink(missing_ok=True)
            self.latest_job_path = job_path
            return None

    def job_path(self, job_id):
        identifier(job_id)
        matches = list(self.store.root.glob(f"*/runs/{job_id}/job.json"))
        if len(matches) != 1:
            raise ProfileError("Training job not found.")
        return matches[0]

    def status(self, job_id=None):
        active = self.active_job()
        if job_id:
            job = read_json(self.job_path(job_id))
        elif active:
            job = active
        elif self.latest_job_path and self.latest_job_path.exists():
            job = read_json(self.latest_job_path)
        else:
            jobs = list(self.store.root.glob("*/runs/*/job.json"))
            job = read_json(max(jobs, key=lambda p: p.stat().st_mtime)) if jobs else None
        return {"job": self.public_job(job) if job else None, "active": active is not None}

    def public_job(self, job):
        result = {key: job.get(key) for key in ("job_id", "profile_id", "status", "max_steps", "error", "warnings")}
        output = within(self.store.profile_dir(job["profile_id"]), job["output_relative"])
        report = output / "training_health.json"
        health = {}
        if report.exists():
            try:
                health = read_json(report)
            except (OSError, ValueError):
                pass
        result["current_step"] = health.get("completed_updates", 0)
        if not result["current_step"] and health.get("updates"):
            result["current_step"] = health["updates"][-1]["step"]
        result["message"] = {"preparing": "Preparing local training and releasing idle models.",
            "running": "Training locally; generation is paused until completion.",
            "completed": "Diagnostic passed; previous adapter retained." if job["max_steps"] == 2 else "Training completed. Adapter is available for this profile.",
            "failed": job.get("error") or "Training failed; any previous successful adapter is preserved."}.get(job["status"], job["status"])
        if job.get("finalizing"):
            result["message"] = "Training finished; waiting for the isolated process to release its GPU memory."
        log = output.parent / "training.log"
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 6000))
                result["log_tail"] = stream.read().decode("utf-8", errors="replace")[-4000:]
        else:
            result["log_tail"] = ""
        return result

    def reserve(self, profile_id, steps):
        if type(steps) is not int or not 1 <= steps <= 2000:
            raise ProfileError("Training steps must be an integer between 1 and 2000; use 2 for a diagnostic.")
        with self.lock:
            if self.active_job():
                raise ProfileError("Another identity training job is already running.")
            profile = self.store.load(profile_id)
            if not selected_photos(profile):
                raise ProfileError("Select at least one valid photo before training.")
            training = self.store.config["training_root"]
            for required in (".venv/Scripts/python.exe", "trainer/train_identity_qwen21.py", "trainer/nf4.json", "models/Qwen-Image-2.1/model_index.json"):
                if not (training / required).is_file():
                    raise ProfileError(f"Required local training file is missing: {required}. No download was attempted.")
            job_id = "run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            run_dir = self.store.profile_dir(profile_id) / "runs" / job_id
            run_dir.mkdir(parents=True, exist_ok=False)
            job = {"job_id": job_id, "profile_id": profile_id, "status": "preparing", "created_at": time.time(),
                   "max_steps": steps, "error": None, "warnings": data_warnings(profile),
                   "output_relative": f"runs/{job_id}/output", "selected_photo_ids": [p["id"] for p in selected_photos(profile)],
                   "pid": None, "launcher_pid": None, "launch_token": uuid.uuid4().hex}
            job_path = run_dir / "job.json"
            write_json(job_path, job)
            with self.lock_path.open("x", encoding="utf-8") as stream:
                json.dump({"job_relative": str(job_path.relative_to(self.store.root))}, stream)
            profile["training"] = {"status": "preparing", "job_id": job_id}
            self.store.save(profile)
            self.latest_job_path = job_path
            return job

    def fail_reserved(self, job, error):
        # If startup failed after a child began, do not release GPU ownership.
        # The worker's reservation handshake times out safely if not completed.
        if process_alive(job.get("pid")) or process_alive(job.get("launcher_pid")):
            job.update(error=str(error))
            write_json(self.job_path(job["job_id"]), job)
            return
        job.update(status="failed", error=str(error))
        write_json(self.job_path(job["job_id"]), job)
        profile = self.store.load(job["profile_id"])
        profile["training"] = {"status": "failed", "job_id": job["job_id"]}
        self.store.save(profile)
        self.lock_path.unlink(missing_ok=True)

    def launch(self, job):
        training = self.store.config["training_root"]
        job_path = self.job_path(job["job_id"])
        environment = os.environ.copy()
        environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "WANDB_DISABLED": "true", "WANDB_MODE": "disabled",
            "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        with (job_path.parent / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(training / ".venv/Scripts/python.exe"), str(Path(__file__).with_name("training_worker.py")),
                "--config", str(self.store.config_path), "--job", str(job_path), "--launch-token", job["launch_token"]], cwd=training, env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # On Windows this is the venv launcher PID. The worker registers its own
        # interpreter PID after validating the private reservation token.
        job.update(pid=process.pid, launcher_pid=process.pid, status="running")
        write_json(job_path, job)
        # Reap the child without blocking ComfyUI's event loop.
        threading.Thread(target=process.wait, daemon=True, name="identity-training-reaper").start()
        return job


def local_request_allowed(request, mutation=False):
    def loopback(value):
        if value == "localhost":
            return True
        try:
            address = ipaddress.ip_address(value or "")
            return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
        except ValueError:
            return False
    if not loopback(request.remote):
        return False
    try:
        host = urlsplit("//" + request.host)
        if not loopback(host.hostname):
            return False
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or not loopback(parsed.hostname) or parsed.netloc.lower() != host.netloc.lower():
                return False
    except ValueError:
        return False
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return False
    return not mutation or request.headers.get("X-Universal-Identity") == "1"


def register_routes(server, store, manager):
    from aiohttp import web
    submission_lock = asyncio.Lock()

    @web.middleware
    async def identity_guard(request, handler):
        path = request.path.removeprefix("/api")
        if path.startswith(BASE_ROUTE):
            if not local_request_allowed(request, request.method not in {"GET", "HEAD"}):
                return web.json_response({"error": "Identity profiles are available only to this local ComfyUI origin."}, status=403)
            try:
                return await handler(request)
            except (ProfileError, ValueError, KeyError, OSError) as error:
                return web.json_response({"error": str(error)}, status=400)
        if request.method == "POST" and path == "/prompt":
            async with submission_lock:
                if manager.active_job():
                    return web.json_response({"error": {"type": "identity_training_active", "message": "Identity training is using the GPU. Wait for it to finish before generating.", "details": "", "extra_info": {}}, "node_errors": {}}, status=409)
                return await handler(request)
        return await handler(request)

    server.app.middlewares.append(identity_guard)
    routes = server.routes

    @routes.get(BASE_ROUTE + "/profiles")
    async def profiles(request):
        return web.json_response({"profiles": store.list_profiles()})

    @routes.post(BASE_ROUTE + "/profiles")
    async def upload(request):
        if manager.active_job():
            raise ProfileError("Wait for training to finish before uploading a new profile.")
        reader = await request.multipart()
        files = []
        name = "Identity profile"
        total = 0
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=store.root) as stage:
            async for part in reader:
                if part.name == "name" and not part.filename:
                    raw = await part.read_chunk(4096)
                    name = raw.decode("utf-8", errors="replace")[:80]
                    await part.release()
                    continue
                if part.name != "files" or not part.filename:
                    raise ProfileError("Use a name field and one or more files fields for photo uploads.")
                destination = Path(stage) / str(len(files))
                size = 0
                with destination.open("wb") as output:
                    while chunk := await part.read_chunk():
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                            raise ProfileError("Upload exceeds the 20 MiB per-file or 200 MiB total limit.")
                        output.write(chunk)
                files.append((part.filename, destination))
            profile = await asyncio.to_thread(store.create, name, files)
        return web.json_response({"profile": store.public(profile)}, status=201)

    @routes.get(BASE_ROUTE + "/profiles/{profile_id}")
    async def profile_get(request):
        return web.json_response({"profile": store.public(store.load(request.match_info["profile_id"]))})

    @routes.patch(BASE_ROUTE + "/profiles/{profile_id}")
    async def profile_patch(request):
        if manager.active_job():
            raise ProfileError("Wait for training to finish before changing photo selections.")
        changes = await request.json()
        if not isinstance(changes, dict):
            raise ProfileError("Profile changes must be a JSON object.")
        return web.json_response({"profile": store.public(store.update(request.match_info["profile_id"], changes))})

    @routes.get(BASE_ROUTE + "/profiles/{profile_id}/photos/{photo_id}")
    async def photo_get(request):
        profile = store.load(request.match_info["profile_id"])
        path = store.photo_path(profile, request.match_info["photo_id"])
        if request.query.get("thumbnail") == "1":
            with Image.open(path) as image:
                image.thumbnail((256, 256))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=85)
            return web.Response(body=buffer.getvalue(), content_type="image/jpeg", headers={"Cache-Control": "no-store"})
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    @routes.get(BASE_ROUTE + "/status")
    async def status_get(request):
        return web.json_response(manager.status())

    @routes.get(BASE_ROUTE + "/jobs/{job_id}")
    async def job_get(request):
        return web.json_response({"job": manager.status(request.match_info["job_id"])["job"]})

    @routes.post(BASE_ROUTE + "/profiles/{profile_id}/train")
    async def train(request):
        changes = await request.json()
        if not isinstance(changes, dict):
            raise ProfileError("Training settings must be a JSON object.")
        async with submission_lock:
            if server.prompt_queue.get_tasks_remaining():
                return web.json_response({"error": "ComfyUI has running or queued work. Finish or clear it before training."}, status=409)
            job = manager.reserve(request.match_info["profile_id"], changes.get("steps", 400))
            try:
                # Reserve first: the node validator and submission middleware now block inference.
                import comfy.model_management as model_management
                model_management.unload_all_models()
                model_management.soft_empty_cache()
                job = manager.launch(job)
            except Exception as error:
                manager.fail_reserved(job, error)
                raise
        return web.json_response({"job": manager.public_job(job)}, status=202)
