"""Outbound-only Firebase job consumer for one local ComfyUI GPU.

Run from this directory: python worker.py --help. No credentials are stored here.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from PIL import Image, ImageOps

try:
    from .body_templates import ALL_BODY_TEMPLATE_IDS, BODY_STYLES, body_catalog_paths, select_body_template
    from .comfy import ComfyClient, ComfyError, LocalProfiles, patch_graph, patch_personal_graph, patch_styled_graph, patch_swap_graph, safe_id
    from .parsing import DEFAULT_MODEL as PARSING_MODEL
    from .personal import CompositeError, PersonalBaseMixin, personal_paths
    from .photo_selection import select_best_photos
    from .selfie import validate_selfie
except ImportError:
    from body_templates import ALL_BODY_TEMPLATE_IDS, BODY_STYLES, body_catalog_paths, select_body_template
    from comfy import ComfyClient, ComfyError, LocalProfiles, patch_graph, patch_personal_graph, patch_styled_graph, patch_swap_graph, safe_id
    from parsing import DEFAULT_MODEL as PARSING_MODEL
    from personal import CompositeError, PersonalBaseMixin, personal_paths
    from photo_selection import select_best_photos
    from selfie import validate_selfie


ROOT = Path(__file__).resolve().parents[2]
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 20
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_ADAPTER_BYTES = 256 * 1024 * 1024
SPLIT_TRAINING_STEPS = 80
POLL_SECONDS = 1
QUEUED_PROGRESS_INTERVAL = 10
STYLED_TEMPLATE = ROOT / "comfy-identity/Qwen21_Garment_Styled_2K.api.json"
SWAP_TEMPLATE = ROOT / "comfy-identity/FaceSwap_TryOn_2K.api.json"
PERSONAL_TEMPLATE = ROOT / "comfy-identity/Qwen21_Personal_Base_2K.api.json"
GPU_KINDS = frozenset({"train", "finalize", "enroll"})
CPU_KINDS = frozenset({"generate"})  # Personal-base try-ons are pure CPU compositing.


class JobError(ValueError):
    """An actionable message safe to show to the account owner."""


class LostLease(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def upload_uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise JobError("Upload a new set of photos before continuing.") from None
    return value


def identity_paths(uid, version, reference_version=None):
    safe_id(uid)
    safe_id(version)
    prefix = f"users/{uid}/identity/{version}"
    reference_prefix = prefix if reference_version is None else prefix + "/references/" + upload_uuid(reference_version)
    return {"adapterPath": prefix + "/adapter.safetensors", "referencePath": reference_prefix + "/reference.png",
            "manifestPath": reference_prefix + "/manifest.json"}


def faceswap_paths(uid, version):
    prefix = f"users/{safe_id(uid)}/identity/{safe_id(version)}"
    return {"referencePath": prefix + "/reference.png", "manifestPath": prefix + "/manifest.json"}


def pending_manifest_path(uid, version):
    return f"users/{safe_id(uid)}/identity/{safe_id(version)}/training.json"


def previous_ready_identity(uid, value):
    if not isinstance(value, dict) or value.get("status") != "ready":
        return None
    try:
        expected = identity_paths(uid, value.get("version"), value.get("referenceVersion"))
    except ValueError:
        return None
    return dict(value) if all(value.get(key) == path for key, path in expected.items()) else None


def failed_identity(uid, version, message, previous=None):
    previous = previous_ready_identity(uid, previous)
    if previous:
        return previous | {"lastTrainingError": message, "updatedAt": now()}
    return {"status": "failed", "version": version, "error": message, "updatedAt": now()}


def validate_selected_reference(job):
    """Version-3 source/selection rules, relative to the request's own submission time."""
    capture, created = job.get("selfieCapturedAt"), job.get("createdAt")
    def recent(stamp):
        return (isinstance(stamp, datetime) and stamp.utcoffset() is not None
                and isinstance(created, datetime) and created.utcoffset() is not None
                and created - timedelta(hours=1) <= stamp <= created + timedelta(minutes=2))
    selected, source = job.get("selfieSelectedAt"), job.get("selfieSource")
    if not recent(selected) or source not in ("camera", "upload"):
        raise JobError("The selfie selection is missing or invalid. Take or choose a clear recent selfie and submit again.")
    if source == "camera" and (not recent(capture) or capture > selected):
        raise JobError("The selfie capture timestamp is missing or invalid. Take or choose a clear recent selfie and submit again.")
    if source == "upload" and "selfieCapturedAt" in job:
        raise JobError("A chosen selfie must record its selection time, without claiming a camera capture time.")


def validate_job(job_id, job):
    safe_id(job_id)
    uid = safe_id(job.get("uid"))
    if (type(job.get("requestVersion")) is not int or job["requestVersion"] not in {1, 2, 3, 4, 5}
            or job.get("kind") not in {"train", "generate", "finalize", "enroll"}):
        raise JobError("Unsupported job request. Reload the app and submit again.")
    if job["kind"] == "enroll":
        # Version 5: one selfie, no personal training. The selfie is the face-swap identity.
        if job["requestVersion"] != 5:
            raise JobError("Reload the app and take or choose your selfie again.")
        upload_id = upload_uuid(job.get("uploadId"))
        if job.get("selfiePath") != f"users/{uid}/uploads/{upload_id}/selfie.jpg" or "photoPaths" in job:
            raise JobError("Take or choose a clear recent selfie for this account before continuing.")
        validate_selected_reference(job)
        return uid
    if job["requestVersion"] == 5 and job["kind"] != "generate":
        raise JobError("Unsupported job request. Reload the app and submit again.")
    if job["kind"] == "train":
        if job["requestVersion"] not in {2, 3, 4}:
            raise JobError("Reload onboarding and take or choose a clear recent selfie before training.")
        upload_id = upload_uuid(job.get("uploadId"))
        expected = [f"users/{uid}/uploads/{upload_id}/{index}.jpg" for index in range(8)]
        if job.get("photoPaths") != expected:
            raise JobError("Training needs exactly eight photos from this account's upload.")
        if job["requestVersion"] == 4:
            if any(key in job for key in ("selfiePath", "selfieSource", "selfieSelectedAt", "selfieCapturedAt", "heightCm", "weightKg", "steps")):
                raise JobError("Reload the app to start photo-only training.")
            return uid
        if job.get("selfiePath") != f"users/{uid}/uploads/{upload_id}/selfie.jpg":
            raise JobError("Take or choose a clear recent selfie for this account's current upload before training.")
        capture, created = job.get("selfieCapturedAt"), job.get("createdAt")
        def recent(stamp):
            return (isinstance(stamp, datetime) and stamp.utcoffset() is not None
                    and isinstance(created, datetime) and created.utcoffset() is not None
                    and created - timedelta(hours=1) <= stamp <= created + timedelta(minutes=2))
        if job["requestVersion"] == 2:
            if not recent(capture) or "selfieSource" in job or "selfieSelectedAt" in job:
                raise JobError("The selfie capture timestamp is missing or invalid. Take or choose a clear recent selfie and submit again.")
        else:
            validate_selected_reference(job)
    elif job["kind"] == "finalize":
        if job["requestVersion"] != 4:
            raise JobError("Unsupported reference finalization request.")
        safe_id(job.get("trainingJobId"))
    else:
        safe_id(job.get("garmentId"))
    return uid


def validate_finalize_context(uid, training_id, user, training, draft):
    identity = user.get("identity") or {}
    if (user.get("trainingJobId") != training_id or identity.get("version") != training_id
            or identity.get("status") != "awaiting_reference"):
        raise JobError("This trained profile is no longer waiting for a reference. Reload onboarding.")
    if (not training or training.get("uid") != uid or training.get("kind") != "train"
            or training.get("requestVersion") != 4 or training.get("status") != "completed"):
        raise JobError("Finish training your uploaded photos before saving the reference.")
    if user.get("consentVersion") != "identity-training-v1" or not user.get("consentAt"):
        raise JobError("Accept the identity-training consent before continuing.")
    if (type(user.get("heightCm")) not in (int, float) or not 80 <= user["heightCm"] <= 250
            or type(user.get("weightKg")) not in (int, float) or not 25 <= user["weightKg"] <= 300
            or user.get("measurementSystem") not in ("us", "metric")):
        raise JobError("Save your height and weight before finishing onboarding.")
    if not isinstance(draft, dict) or draft.get("trainingJobId") != training_id:
        raise JobError("Take or choose a clear recent selfie before finishing onboarding.")
    upload_uuid(draft.get("uploadId"))
    # Reuse the reviewed v3 source/capture validation, relative to draft submission,
    # so waiting in the queue never makes an originally valid choice expire.
    reference_job = {"uid": uid, "kind": "train", "requestVersion": 3,
                     "uploadId": draft["uploadId"], "createdAt": draft.get("submittedAt"),
                     "photoPaths": [f"users/{uid}/uploads/{draft['uploadId']}/{i}.jpg" for i in range(8)]}
    reference_job.update({key: draft[key] for key in ("selfiePath", "selfieSource", "selfieSelectedAt", "selfieCapturedAt") if key in draft})
    validate_job(training_id, reference_job)
    return identity


def validate_garment(garment_id, garment, body_template_id=None):
    safe_id(garment_id)
    if not garment or garment.get("active") is not True:
        raise JobError("This garment is no longer available.")
    expected = {"imagePath": f"garments/{garment_id}/reference.png", "baseImagePath": f"garments/{garment_id}/base.png"}
    if any(garment.get(key) != path for key, path in expected.items()):
        raise JobError("This garment needs its catalog images configured by the project owner.")
    if body_template_id is not None:
        try:
            return body_catalog_paths(garment_id, garment, body_template_id)
        except ValueError as error:
            raise JobError("This piece is not configured for your body template yet. Please try another piece.") from error
    return expected


def configured_body_templates(garment):
    """Every template this garment publishes (male, plus female once seeded), or the legacy base."""
    paths = garment.get("bodyBaseImagePaths") if isinstance(garment, dict) else None
    if not isinstance(paths, dict):
        return (None,)
    return tuple(key for key in ALL_BODY_TEMPLATE_IDS if key in paths)


def saved_body_template(identity):
    """Use the enrollment snapshot, never later edits to the account's measurements."""
    if "bodyTemplate" not in identity:
        return None  # Earlier personal bases remain attached to the original catalog pose.
    snapshot = identity["bodyTemplate"]
    if not isinstance(snapshot, dict):
        raise JobError("Your saved body template is incomplete. Set up your look again.")
    try:
        expected = select_body_template(snapshot.get("heightCm"), snapshot.get("weightKg"),
                                        policy_version=snapshot.get("policyVersion"), style=snapshot.get("bodyStyle", "male"))
    except (ValueError, TypeError):
        raise JobError("Your saved measurements are invalid. Set up your look again.") from None
    if any(snapshot.get(key) != value for key, value in expected.items()):
        raise JobError("Your saved body template does not match its measurements. Set up your look again.")
    return expected["id"]


def clean_image(data, destination):
    if len(data) > MAX_IMAGE_BYTES:
        raise JobError("An input image is larger than 20 MiB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise JobError("An input image exceeds 40 megapixels.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
            image.info.clear()
            image.save(destination, format="PNG")
    except (OSError, Image.DecompressionBombError):
        raise JobError("An input image could not be decoded. Replace it and try again.") from None
    return Path(destination)


def clean_output(data, image_format="PNG"):
    # Re-encode without Comfy workflow metadata or signed download tokens.
    # The 2K result is a high-quality JPEG so it uploads in about a second instead of several.
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 70_000_000:
            raise JobError("The rendered image exceeds the allowed output size.")
        image = source.convert("RGB")
        image.info.clear()
        output = io.BytesIO()
        if image_format == "JPEG":
            image.save(output, format="JPEG", quality=94, subsampling=0)
        else:
            image.save(output, format="PNG")
        return output.getvalue(), image.size


class FirebaseStore:
    """All job/user/history writes are fenced by the active job lease."""
    def __init__(self, project_id, bucket_name):
        import firebase_admin
        from firebase_admin import firestore, storage
        from google.cloud.firestore_v1.base_query import FieldFilter
        self.fs = firestore
        self.filter = FieldFilter
        self.app = firebase_admin.initialize_app(options={"projectId": project_id, "storageBucket": bucket_name})
        self.db = firestore.client(app=self.app)
        self.bucket = storage.bucket(app=self.app)

    def transaction(self, callback):
        return self.fs.transactional(callback)(self.db.transaction())

    def user(self, uid):
        return self.db.collection("users").document(safe_id(uid)).get().to_dict() or {}

    def onboarding(self, uid):
        return self.db.collection("users").document(safe_id(uid)).collection("onboarding").document("current").get().to_dict()

    def job(self, job_id):
        return self.db.collection("jobs").document(safe_id(job_id)).get().to_dict()

    def garment(self, garment_id):
        return self.db.collection("garments").document(safe_id(garment_id)).get().to_dict()

    def queued_enrollment_progress(self, preparation=None, *, unavailable=False):
        """A bounded startup notice, transactionally fenced to still-unclaimed enrollment jobs."""
        if preparation is not None:
            completed, total = preparation.get("completed"), preparation.get("total")
            if type(completed) is not int or type(total) is not int or not 0 <= completed <= total <= 250:
                raise ValueError("Invalid catalog preparation counts.")
        elif unavailable:
            raise ValueError("Unavailable preparation requires observed counts.")
        candidates = self.db.collection("jobs").where(filter=self.filter("status", "==", "queued")).limit(100).stream()
        changed = 0
        for index, candidate in enumerate(candidates):
            if index >= 100:
                break
            def write(transaction):
                job = candidate.reference.get(transaction=transaction).to_dict()
                if (not job or job.get("status") != "queued" or job.get("kind") != "enroll"
                        or job.get("requestVersion") != 5 or job.get("leaseOwner") or job.get("leaseToken")):
                    return False
                try:
                    safe_id(job.get("uid"))
                except ValueError:
                    return False
                stamp = now()
                message = (f"Preparing shared clothing presets: {completed} of {total} ready. "
                           "This setup is shared by everyone; your personal look has not started yet.") if preparation is not None else (
                           "Clothing setup has finished. Waiting for the worker to start your personal look.")
                if unavailable:
                    message = (f"Some shared clothing presets could not be prepared: {completed} of {total} ready. "
                               "The worker will check your selected pieces before starting your look.")
                transaction.update(candidate.reference, {"stage": "preparing_catalog" if preparation is not None else "waiting_for_worker",
                                   "message": message, "preparation": dict(preparation) if preparation is not None else None,
                                   "sampling": None, "progress": None, "updatedAt": stamp, "workerSeenAt": stamp})
                return True
            changed += int(self.transaction(write))
        return changed

    def claim(self, worker_id, kinds=None):
        candidates = self.db.collection("jobs").where(filter=self.filter("status", "==", "queued")).limit(20).stream()
        for candidate in candidates:
            def take(transaction):
                document = candidate.reference.get(transaction=transaction)
                job = document.to_dict()
                if not job or job.get("status") != "queued":
                    return None
                if kinds is not None and job.get("kind") not in kinds and job.get("kind") in GPU_KINDS | CPU_KINDS:
                    return None
                try:
                    validate_job(candidate.id, job)
                except ValueError:
                    transaction.update(candidate.reference, {"status": "failed", "stage": "invalid_request",
                                       "error": "The job request is invalid. Submit it again from the app.", "updatedAt": now()})
                    return None
                token = uuid.uuid4().hex
                updates = {"status": "running", "stage": "starting", "progress": 0.0,
                           "message": "Local worker accepted the job.", "updatedAt": now(), "startedAt": now(),
                           "workerSeenAt": now(), "preparation": None, "sampling": None,
                           "leaseOwner": worker_id, "leaseToken": token,
                           "leaseExpiresAt": now() + timedelta(seconds=LEASE_SECONDS)}
                transaction.update(candidate.reference, updates)
                return candidate.id, job | updates
            claimed = self.transaction(take)
            if claimed:
                return claimed
        return None

    def update(self, job_id, owner, token, fields=None, *, user_fields=None, generation=None, finalization=None):
        reference = self.db.collection("jobs").document(safe_id(job_id))
        def write(transaction):
            job = reference.get(transaction=transaction).to_dict()
            stamp = now()
            if (not job or job.get("status") != "running" or job.get("leaseOwner") != owner
                    or job.get("leaseToken") != token or job.get("leaseExpiresAt", stamp) <= stamp):
                raise LostLease("Job ownership expired; no further work will be submitted.")
            uid = safe_id(job["uid"])
            user_ref = self.db.collection("users").document(uid)
            if finalization is not None:
                training_id = safe_id(finalization["trainingJobId"])
                user = user_ref.get(transaction=transaction).to_dict() or {}
                training = self.db.collection("jobs").document(training_id).get(transaction=transaction).to_dict()
                draft = user_ref.collection("onboarding").document("current").get(transaction=transaction).to_dict()
                validate_finalize_context(uid, training_id, user, training, draft)
                if draft != finalization["draft"]:
                    raise JobError("Your reference changed while being saved. Submit the current selfie again.")
            updates = {"updatedAt": stamp, "workerSeenAt": stamp, "leaseExpiresAt": stamp + timedelta(seconds=LEASE_SECONDS)} | (fields or {})
            transaction.update(reference, updates)
            if user_fields is not None:
                transaction.update(user_ref, {"updatedAt": stamp} | user_fields)
            if generation is not None:
                destination = self.db.collection("users").document(uid).collection("generations").document(job_id)
                transaction.set(destination, {"uid": uid, "jobId": job_id, "updatedAt": stamp} | generation, merge=True)
            return updates["leaseExpiresAt"]
        return self.transaction(write)

    def expire_abandoned(self):
        # Never reclaim/resubmit running GPU jobs. An ambiguous submission can still be executing.
        candidates = self.db.collection("jobs").where(filter=self.filter("status", "==", "running")).limit(50).stream()
        for candidate in candidates:
            def expire(transaction):
                job = candidate.reference.get(transaction=transaction).to_dict()
                stamp = now()
                if not job or job.get("status") != "running" or job.get("leaseExpiresAt", stamp) >= stamp:
                    return
                uid = job.get("uid")
                try:
                    user_ref = self.db.collection("users").document(safe_id(uid))
                except ValueError:
                    user_ref = None
                user = (user_ref.get(transaction=transaction).to_dict() or {}) if user_ref else {}
                message = "The local worker stopped responding. Check the local GPU worker, then submit a new job."
                transaction.update(candidate.reference, {"status": "failed", "stage": "worker_unavailable",
                                   "error": message, "message": message, "updatedAt": stamp})
                if user_ref and job.get("kind") == "train" and user.get("trainingJobId") == candidate.id:
                    identity = failed_identity(uid, candidate.id, message, job.get("previousIdentity"))
                    transaction.update(user_ref, {"identity": identity, "updatedAt": stamp})
                if user_ref and job.get("kind") == "generate":
                    transaction.set(user_ref.collection("generations").document(candidate.id),
                                    {"uid": uid, "jobId": candidate.id, "garmentId": job.get("garmentId"),
                                     "status": "failed", "error": message, "updatedAt": stamp}, merge=True)
            self.transaction(expire)

    def active_garments(self):
        query = self.db.collection("garments").where(filter=self.filter("active", "==", True)).limit(50)
        return [(document.id, document.to_dict()) for document in query.stream()]

    def download(self, path, max_bytes):
        blob = self.bucket.blob(path)
        blob.reload()
        if blob.size is None or not 0 < blob.size <= max_bytes:
            raise JobError("A stored input is empty or exceeds its size limit.")
        # Generation precondition prevents reading a replacement after validating the size.
        data = blob.download_as_bytes(if_generation_match=blob.generation, raw_download=True)
        if len(data) > max_bytes:
            raise JobError("A stored input exceeds its size limit.")
        return data

    def upload(self, path, data, content_type):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(path)
        blob.cache_control = "private, no-store"
        blob.metadata = {"sha256": sha256(data)}  # No Firebase public download token.
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
        except PreconditionFailed:
            # An upload response can be lost after success. Accept only the same immutable bytes.
            blob.reload()
            if blob.size != len(data) or (blob.metadata or {}).get("sha256") != sha256(data):
                raise JobError("A saved output already exists with different content.") from None


class Lease:
    def __init__(self, store, job_id, job):
        self.store, self.job_id = store, job_id
        self.owner, self.token = job["leaseOwner"], job["leaseToken"]
        self.expires_at = job["leaseExpiresAt"]
        self.stopped = threading.Event()
        self.lost = threading.Event()

    def __enter__(self):
        def heartbeat():
            while not self.stopped.wait(HEARTBEAT_SECONDS):
                try:
                    expiry = self.store.update(self.job_id, self.owner, self.token)
                    self.expires_at = max(self.expires_at, expiry)
                except Exception:
                    self.lost.set()
                    logging.exception("Job heartbeat failed; stopping further submission.")
                    break
        self.thread = threading.Thread(target=heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=2)

    def guard(self):
        if self.lost.is_set() or now() >= self.expires_at:
            raise LostLease("Job lease was lost.")

    def write(self, fields=None, **kwargs):
        self.guard()
        expiry = self.store.update(self.job_id, self.owner, self.token, fields, **kwargs)
        self.expires_at = max(self.expires_at, expiry)
        return expiry


class Worker(PersonalBaseMixin):
    def __init__(self, store, comfy, profiles, template, state_dir, selector=select_best_photos, selfie_validator=validate_selfie,
                 *, pipeline="faceswap", styled_template=None, swap_template=None, personal_template=None):
        self.store, self.comfy, self.profiles = store, comfy, profiles
        self.template, self.state_dir, self.selector = template, Path(state_dir), selector
        self.selfie_validator = selfie_validator
        if pipeline not in ("faceswap", "qwen"):
            raise ValueError("Unknown generation pipeline.")
        self.pipeline = pipeline
        self.styled_template = styled_template or json.loads(STYLED_TEMPLATE.read_text(encoding="utf-8-sig"))
        self.swap_template = swap_template or json.loads(SWAP_TEMPLATE.read_text(encoding="utf-8-sig"))
        self.personal_template = personal_template or json.loads(PERSONAL_TEMPLATE.read_text(encoding="utf-8-sig"))
        self.comfy_inputs = {}  # Cached garment renders already uploaded to local ComfyUI this session.
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def run_job(self, job_id, job):
        with Lease(self.store, job_id, job) as lease:
            previous_identity = None
            user_read = False
            try:
                uid = validate_job(job_id, job)
                user = self.store.user(uid)
                previous_identity = previous_ready_identity(uid, user.get("identity"))
                user_read = True
                if user.get("consentVersion") != "identity-training-v1" or not user.get("consentAt"):
                    raise JobError("Accept the identity-training consent before continuing.")
                with tempfile.TemporaryDirectory(prefix="job-", dir=self.state_dir) as folder:
                    if job["kind"] == "train":
                        self.train(job_id, job, uid, Path(folder), lease, previous_identity)
                    elif job["kind"] == "enroll":
                        self.enroll(job_id, job, uid, user, Path(folder), lease)
                    elif job["kind"] == "finalize":
                        self.finalize(job_id, job, uid, user, Path(folder), lease)
                    else:
                        self.generate(job_id, job, uid, user, Path(folder), lease)
            except LostLease:
                logging.warning("Job %s lost its lease; it will not be resubmitted.", job_id)
            except Exception as error:
                logging.exception("Job %s failed.", job_id)
                message = str(error) if isinstance(error, (JobError, ComfyError)) else "The local worker could not finish this job. Check its log before retrying."
                fields = {"status": "failed", "stage": "failed", "message": message, "error": message}
                extras = {}
                if job.get("kind") == "train" and user_read:
                    extras["user_fields"] = {"identity": failed_identity(job["uid"], job_id, message, previous_identity)}
                elif job.get("kind") == "generate":
                    extras["generation"] = {"status": "failed", "garmentId": job.get("garmentId"), "error": message}
                try:
                    lease.write(fields, **extras)
                except Exception:
                    logging.exception("Could not publish terminal status; lease expiry will fail the job.")

    def reference(self, job, folder):
        selfie = clean_image(self.store.download(job["selfiePath"], MAX_IMAGE_BYTES), folder / "selfie-reference.png")
        try:
            self.selfie_validator(selfie)
        except ValueError as error:
            raise JobError(str(error)[:800]) from None
        reference_data = selfie.read_bytes()
        if len(reference_data) > MAX_IMAGE_BYTES:
            raise JobError("The normalized selfie exceeds 20 MiB. Take or choose a smaller clear recent selfie.")
        source = "recent_selfie" if job.get("selfieSource") == "upload" else "live_selfie"
        provenance = {"referenceSource": source, "referenceSourcePath": job["selfiePath"],
                      "selfieSelectedAt": job.get("selfieSelectedAt", job.get("selfieCapturedAt"))}
        if source == "live_selfie":
            provenance["selfieCapturedAt"] = job["selfieCapturedAt"]
        return reference_data, provenance

    def train(self, job_id, job, uid, folder, lease, previous_identity=None):
        split = job.get("requestVersion") == 4
        steps = SPLIT_TRAINING_STEPS if split else 400
        identity = {"status": "selecting", "version": job_id, "error": None, "updatedAt": now()}
        lease.write({"stage": "selecting", "message": "Choosing five clear, varied photos from your eight uploads.",
                     "progress": 0.05, "previousIdentity": previous_identity},
                    user_fields={"identity": identity, "trainingJobId": job_id})
        lease.guard()
        if not split:
            reference_data, provenance = self.reference(job, folder)
        paths = []
        for index, storage_path in enumerate(job["photoPaths"]):
            lease.guard()
            path = folder / f"photo-{index}.jpg"
            path.write_bytes(self.store.download(storage_path, MAX_IMAGE_BYTES))
            paths.append(path)
        try:
            selection = self.selector(paths)
        except ValueError as error:
            raise JobError(str(error)[:800]) from None
        if (len(selection) != 8 or {row.get("index") for row in selection} != set(range(8))
                or sum(row.get("selected") is True for row in selection) != 5):
            raise JobError("Photo selection did not produce five usable photos. Replace unclear or duplicate images.")
        selection = [{key: value for key, value in row.items() if key != "path"}
                     | {"sourcePath": job["photoPaths"][row["index"]]} for row in selection]
        selected = [row for row in selection if row["selected"]]
        selected.sort(key=lambda row: (-row.get("score", 0), row["index"]))
        clean = [clean_image(paths[row["index"]].read_bytes(), folder / f"selected-{n}.png")
                 for n, row in enumerate(selected)]
        lease.write({"stage": "preparing", "message": "Preparing your local identity profile.", "progress": 0.12})
        lease.guard()
        profile = self.comfy.create_profile("cloud-" + sha256(uid.encode())[:12] + "-" + job_id[:20], clean)
        profile_id = safe_id(profile["id"])
        self.profiles.mark_owner(profile_id, uid, job_id, require_selfie=True)
        if not split:
            self.profiles.bind_reference(profile_id, uid, job_id, reference_data, source=provenance["referenceSource"])
        identity |= {"status": "training", "profileId": profile_id, "selectedPhotos": selection, "updatedAt": now()}
        lease.write({"stage": "training", "message": f"Training your identity for {steps} steps.", "progress": 0.15,
                     "localProfileId": profile_id, "trainingSteps": steps}, user_fields={"identity": identity})
        lease.guard()
        training = self.comfy.train(profile_id, steps=steps) if split else self.comfy.train(profile_id)
        lease.write({"localTrainingJobId": training["job_id"]})
        last = [-1.0]
        def progress(fraction):
            value = 0.15 + 0.75 * fraction
            if value - last[0] >= 0.02:
                lease.write({"progress": value, "message": f"Training identity: {int(fraction * steps)}/{steps} steps."})
                last[0] = value
        self.comfy.wait_training(training["job_id"], lease.guard, progress, steps=steps)
        adapter, local = self.profiles.adapter(profile_id, uid, job_id)
        if adapter.stat().st_size > MAX_ADAPTER_BYTES:
            raise JobError("The trained adapter exceeds the supported size.")
        adapter_data = adapter.read_bytes()
        destinations = identity_paths(uid, job_id)
        if split:
            manifest_path = pending_manifest_path(uid, job_id)
            manifest = {"schemaVersion": 3, "referencePending": True, "uid": uid, "version": job_id,
                        "profileId": profile_id, "trigger": local["trigger"], "steps": steps,
                        "selectedPhotos": selection, "adapterPath": destinations["adapterPath"],
                        "adapterSha256": sha256(adapter_data)}
            lease.write({"stage": "saving", "message": "Saving your trained identity.", "progress": 0.95})
            self.store.upload(destinations["adapterPath"], adapter_data, "application/octet-stream")
            lease.guard()
            self.store.upload(manifest_path, json.dumps(manifest).encode(), "application/json")
            identity |= {"status": "awaiting_reference", "steps": steps, "adapterPath": destinations["adapterPath"],
                         "trainingManifestPath": manifest_path, "updatedAt": now()}
            lease.write({"status": "completed", "stage": "awaiting_reference", "message": "Training is finished. Complete your measurements and recent selfie.", "progress": 1.0},
                        user_fields={"identity": identity, "trainingJobId": job_id})
            return
        manifest = {"schemaVersion": 2, "uid": uid, "version": job_id, "profileId": profile_id,
                    "trigger": local["trigger"], "steps": steps, "selectedPhotos": selection,
                    **{key: value.isoformat() if isinstance(value, datetime) else value for key, value in provenance.items()},
                    **destinations, "adapterSha256": sha256(adapter_data), "referenceSha256": sha256(reference_data)}
        lease.write({"stage": "saving", "message": "Saving your private identity adapter.", "progress": 0.95})
        for key, data, content_type in [("adapterPath", adapter_data, "application/octet-stream"),
                                        ("referencePath", reference_data, "image/png"),
                                        ("manifestPath", json.dumps(manifest).encode(), "application/json")]:
            lease.guard()
            self.store.upload(destinations[key], data, content_type)
        identity |= {"status": "ready", **destinations, **provenance, "updatedAt": now()}
        lease.write({"status": "completed", "stage": "ready", "message": "Your identity is ready.", "progress": 1.0},
                    user_fields={"identity": identity})

    def finalize(self, job_id, job, uid, user, folder, lease):
        training_id = safe_id(job.get("trainingJobId"))
        draft, training = self.store.onboarding(uid), self.store.job(training_id)
        identity = validate_finalize_context(uid, training_id, user, training, draft)
        fence = {"trainingJobId": training_id, "draft": draft}
        lease.write({"stage": "validating_reference", "message": "Checking your recent selfie.", "progress": 0.1}, finalization=fence)
        manifest_path = pending_manifest_path(uid, training_id)
        adapter_path = identity_paths(uid, training_id)["adapterPath"]
        if identity.get("trainingManifestPath") != manifest_path or identity.get("adapterPath") != adapter_path:
            raise JobError("The trained identity does not match this account.")
        pending = json.loads(self.store.download(manifest_path, 1024 * 1024))
        if (pending.get("schemaVersion") != 3 or pending.get("referencePending") is not True
                or pending.get("uid") != uid or pending.get("version") != training_id
                or type(pending.get("steps")) is not int or not 1 <= pending["steps"] <= 2000
                or pending["steps"] != identity.get("steps")
                or pending.get("adapterPath") != adapter_path
                or not isinstance(pending.get("trigger"), str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", pending["trigger"])
                or not isinstance(pending.get("adapterSha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", pending["adapterSha256"])):
            raise JobError("The saved trained identity failed ownership validation.")
        reference_data, provenance = self.reference(draft, folder)
        profile_id = identity.get("profileId")
        try:
            adapter, local = self.profiles.adapter(profile_id, uid, training_id)
            if sha256(adapter.read_bytes()) != pending["adapterSha256"] or local.get("trigger") != pending["trigger"]:
                raise ValueError("Cached training does not match its cloud manifest.")
            if not self.comfy.profile(profile_id).get("training", {}).get("adapter_available"):
                raise ValueError("Cached adapter is unavailable.")
        except (ValueError, OSError, ComfyError, KeyError):
            lease.write({"stage": "restoring_identity", "message": "Restoring your trained identity.", "progress": 0.4}, finalization=fence)
            adapter_data = self.store.download(adapter_path, MAX_ADAPTER_BYTES)
            if sha256(adapter_data) != pending["adapterSha256"]:
                raise JobError("The saved trained adapter failed integrity validation.")
            reference = folder / "selfie-reference.png"
            lease.guard()
            profile = self.comfy.create_profile("cloud-" + sha256(uid.encode())[:12] + "-" + training_id[:20], [reference])
            profile_id = safe_id(profile["id"])
            self.profiles.restore(profile_id, uid, pending, adapter_data)
        lease.guard()
        self.profiles.bind_reference(profile_id, uid, training_id, reference_data, source=provenance["referenceSource"])
        if self.comfy.profile(profile_id).get("reference_source") != provenance["referenceSource"]:
            raise JobError("The local identity extension needs its selfie-reference update before continuing.")
        reference_version = draft["uploadId"]
        destinations = identity_paths(uid, training_id, reference_version)
        manifest = {key: value for key, value in pending.items() if key not in ("schemaVersion", "referencePending")}
        manifest |= {"schemaVersion": 2, "referenceVersion": reference_version, **destinations,
                     **{key: value.isoformat() if isinstance(value, datetime) else value for key, value in provenance.items()},
                     "referenceSha256": sha256(reference_data)}
        lease.write({"stage": "saving_reference", "message": "Saving your current look.", "progress": 0.8}, finalization=fence)
        self.store.upload(destinations["referencePath"], reference_data, "image/png")
        lease.guard()
        self.store.upload(destinations["manifestPath"], json.dumps(manifest).encode(), "application/json")
        ready = identity | {"status": "ready", "profileId": profile_id, "steps": pending["steps"],
                            "referenceVersion": reference_version, **destinations, **provenance,
                            "error": None, "updatedAt": now()}
        lease.write({"status": "completed", "stage": "ready", "message": "Your identity is ready.", "progress": 1.0},
                    user_fields={"identity": ready}, finalization=fence)

    def identity_profile(self, uid, identity, folder, lease):
        if not isinstance(identity, dict) or identity.get("status") != "ready":
            raise JobError("Train your identity before generating a try-on.")
        version = safe_id(identity.get("version"))
        reference_version = identity.get("referenceVersion")
        expected = identity_paths(uid, version, reference_version)
        if any(identity.get(key) != path for key, path in expected.items()):
            raise JobError("The saved identity does not match this account. Train it again.")
        manifest = json.loads(self.store.download(expected["manifestPath"], 1024 * 1024))
        if (type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] not in {1, 2}
                or manifest.get("uid") != uid or manifest.get("version") != version
                or type(manifest.get("steps")) is not int or not 1 <= manifest["steps"] <= 2000
                or manifest.get("referenceVersion") != reference_version
                or any(manifest.get(key) != path for key, path in expected.items())):
            raise JobError("The saved identity manifest failed ownership validation.")
        selfie = manifest["schemaVersion"] == 2
        if selfie:
            source = manifest.get("referenceSourcePath")
            match = re.fullmatch(re.escape(f"users/{uid}/uploads/") + r"([0-9a-f-]{36})/selfie\.jpg", source or "")
            try:
                valid_source = bool(match and str(uuid.UUID(match[1])) == match[1])
                if reference_version is not None:
                    valid_source = valid_source and match[1] == reference_version
                reference_source = manifest.get("referenceSource")
                selected = manifest.get("selfieSelectedAt")
                if reference_source == "live_selfie":
                    capture = datetime.fromisoformat(manifest.get("selfieCapturedAt", ""))
                    valid_source = valid_source and capture.utcoffset() is not None
                    # Pre-choice schema-2 camera manifests did not record selection time.
                    if "selfieSelectedAt" in manifest:
                        selected = datetime.fromisoformat(selected)
                        valid_source = valid_source and selected.utcoffset() is not None and capture <= selected
                elif reference_source == "recent_selfie":
                    selected = datetime.fromisoformat(selected)
                    valid_source = valid_source and selected.utcoffset() is not None and "selfieCapturedAt" not in manifest
                else:
                    valid_source = False
            except (ValueError, TypeError):
                valid_source = False
            if not valid_source:
                raise JobError("This identity is missing its required selfie reference. Complete onboarding again.")
        profile_id = identity.get("profileId")
        try:
            adapter, local = self.profiles.adapter(profile_id, uid, version)
            if sha256(adapter.read_bytes()) != manifest.get("adapterSha256") or local.get("trigger") != manifest.get("trigger"):
                raise ValueError("Local adapter differs from saved cloud identity.")
            if selfie:
                self.profiles.verify_reference(profile_id, uid, version, manifest.get("referenceSha256"), source=reference_source)
            public = self.comfy.profile(profile_id)
            if public.get("training", {}).get("adapter_available") and (not selfie or public.get("reference_source") == reference_source):
                return profile_id
        except (ValueError, OSError, ComfyError, KeyError):
            pass
        lease.write({"stage": "restoring_identity", "message": "Restoring your saved identity to this GPU worker.", "progress": 0.08})
        adapter_data = self.store.download(expected["adapterPath"], MAX_ADAPTER_BYTES)
        reference_data = self.store.download(expected["referencePath"], MAX_IMAGE_BYTES)
        if sha256(adapter_data) != manifest.get("adapterSha256") or sha256(reference_data) != manifest.get("referenceSha256"):
            raise JobError("Saved identity files failed integrity validation.")
        reference = clean_image(reference_data, folder / "identity-reference.png")
        lease.guard()
        profile = self.comfy.create_profile("cloud-" + sha256(uid.encode())[:12] + "-" + version[:20], [reference])
        profile_id = safe_id(profile["id"])
        self.profiles.restore(profile_id, uid, manifest, adapter_data, reference_bytes=reference_data if selfie else None)
        if selfie and self.comfy.profile(profile_id).get("reference_source") != reference_source:
            raise JobError("The local identity extension needs its selfie-reference update before generation can continue.")
        lease.write(user_fields={"identity": identity | {"profileId": profile_id, "updatedAt": now()}})
        return profile_id

    def enroll(self, job_id, job, uid, user, folder, lease):
        """Selfie onboarding: check the selfie, then build the person's personal base for each pose base."""
        if (type(user.get("heightCm")) not in (int, float) or not 80 <= user["heightCm"] <= 250
                or type(user.get("weightKg")) not in (int, float) or not 25 <= user["weightKg"] <= 300
                or user.get("measurementSystem") not in ("us", "metric")):
            raise JobError("Save your height and weight before finishing onboarding.")
        style = user.get("bodyStyle", "male")  # Profiles saved before the male/female choice keep the male set.
        if style not in BODY_STYLES:
            raise JobError("Choose male or female with your height and weight before finishing onboarding.")
        try:
            body_template = select_body_template(user["heightCm"], user["weightKg"], style=style)
        except (ValueError, TypeError):
            raise JobError("Save valid height and weight measurements before finishing onboarding.") from None
        lease.write({"stage": "checking_selfie", "message": "Checking that your selfie has one clear, usable face.", "progress": None, "sampling": None})
        reference_data, provenance = self.reference(job, folder)
        destinations = faceswap_paths(uid, job_id)
        reference_sha = sha256(reference_data)
        self.cache_reference(reference_sha, reference_data)
        lease.write({"stage": "checking_catalog", "message": "Selecting your body template and checking its prepared clothing presets.", "progress": None})
        bases = {}
        for garment_id, garment in self.store.active_garments():
            catalog_paths = validate_garment(garment_id, garment, body_template["id"])
            key, outputs, base_key = self.styled_garment(
                garment_id, garment, catalog_paths, folder, lease.guard,
                lambda message: lease.write({"stage": "checking_catalog", "message": message, "progress": None}),
                allow_render=False)
            bases.setdefault(base_key, []).append((key, outputs))
        if not bases:
            raise JobError("No pieces are available yet. Try again shortly.")
        if len(bases) != 1:
            raise JobError("The catalog body templates do not share the same pose. Please try again after the catalog is corrected.")
        records, uploads = {}, [(destinations["referencePath"], reference_data, "image/png")]
        try:
            for base_key in bases:
                p1024, p2k, hair, tee = self.personal_base(
                    uid, job_id, folder / "selfie-reference.png", base_key, lease.guard,
                    lambda message, progress: lease.write({"stage": "creating_look", "message": message, "progress": progress}),
                    details=lambda fields: lease.write(fields))
                assets = {"p1024": p1024, "p2k": p2k, "hair": hair, "tee": tee}
                self.cache_personal(uid, job_id, base_key, assets)
                paths = personal_paths(uid, job_id, base_key)
                records[base_key] = paths | {name + "Sha256": sha256(data) for name, data in assets.items()}
                uploads += [(paths[name], data, "image/png") for name, data in assets.items()]
        except CompositeError as error:
            raise JobError(str(error)) from None
        manifest = {"schemaVersion": 5, "mode": "personal_base", "uid": uid, "version": job_id, **destinations,
                    **{key: value.isoformat() if isinstance(value, datetime) else value for key, value in provenance.items()},
                    "referenceSha256": reference_sha, "personalBases": records, "bodyTemplate": body_template}
        uploads.append((destinations["manifestPath"], json.dumps(manifest).encode(), "application/json"))
        lease.write({"stage": "saving_look", "message": "Saving your preview and private look files to your account.", "progress": None, "sampling": None})
        with ThreadPoolExecutor(max_workers=4) as pool:
            for upload in [pool.submit(self.store.upload, *item) for item in uploads]:
                upload.result()
        preview = next(iter(records.values()))["p1024"]
        identity = {"status": "ready", "mode": "personal_base", "version": job_id, **destinations, **provenance,
                    "referenceSha256": reference_sha, "personalBases": records, "previewPath": preview,
                    "error": None, "updatedAt": now(), "bodyTemplate": body_template}
        lease.write({"status": "completed", "stage": "ready", "message": "Your fitting room is ready.", "progress": 1.0, "sampling": None},
                    user_fields={"identity": identity})
        # Garment masks are CPU work the first scan would otherwise pay for.
        for base_key, renders in bases.items():
            for key, outputs in renders:
                try:
                    self.garment_masks(key, outputs, base_key)
                except Exception:
                    logging.exception("Could not prepare masks for garment render %s.", key)

    def cache_reference(self, digest, data):
        path = self.state_dir / "references" / f"{digest}.png"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(".ref-" + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        return path

    def identity_reference(self, uid, identity, folder):
        """The owner's verified selfie; works for face-swap and earlier trained identities alike."""
        if not isinstance(identity, dict) or identity.get("status") != "ready":
            raise JobError("Finish setting up your profile before trying something on.")
        version = safe_id(identity.get("version"))
        if identity.get("mode") == "faceswap":
            expected = faceswap_paths(uid, version)
        else:
            expected = {key: value for key, value in identity_paths(uid, version, identity.get("referenceVersion")).items()
                        if key != "adapterPath"}
        if any(identity.get(key) != path for key, path in expected.items()):
            raise JobError("The saved profile does not match this account. Set it up again.")
        digest = identity.get("referenceSha256")
        cached = self.state_dir / "references" / f"{digest}.png" if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None
        if cached is None or not cached.is_file() or sha256(cached.read_bytes()) != digest:
            manifest = json.loads(self.store.download(expected["manifestPath"], 1024 * 1024))
            digest = manifest.get("referenceSha256")
            if (manifest.get("uid") != uid or manifest.get("version") != version
                    or any(manifest.get(key) != path for key, path in expected.items())
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise JobError("The saved profile failed ownership validation. Set it up again.")
            data = self.store.download(expected["referencePath"], MAX_IMAGE_BYTES)
            if sha256(data) != digest:
                raise JobError("Your saved selfie failed integrity validation. Set up your profile again.")
            cached = self.cache_reference(digest, data)
        return clean_image(cached.read_bytes(), folder / "selfie.png")

    def styled_garment(self, garment_id, garment, catalog_paths, folder, guard, notify, *, allow_render=True):
        """Base model wearing this garment (1024 + 2K). Rendered once with Qwen, then cached locally."""
        stamp = garment.get("updatedAt")
        stamp = stamp.isoformat() if isinstance(stamp, datetime) else str(stamp)
        key = sha256(json.dumps([garment_id, catalog_paths, stamp, self.styled_template], sort_keys=True).encode())[:32]
        directory = self.state_dir / "styled" / key
        outputs = directory / "styled-1024.png", directory / "styled-2k.png"
        base_file = directory / "base-key.txt"
        if all(path.is_file() for path in outputs) and base_file.is_file():
            return key, outputs, base_file.read_text().strip()
        if not allow_render and not all(path.is_file() for path in outputs):
            raise JobError("This piece's body template is still being prepared. Please try again shortly.")
        base = clean_image(self.store.download(catalog_paths["baseImagePath"], MAX_IMAGE_BYTES), folder / "base.png")
        base_key = self.register_pose_source(base.read_bytes())
        if all(path.is_file() for path in outputs):
            base_file.write_text(base_key)
            return key, outputs, base_key
        notify("Preparing this piece for the fitting room. This happens once per piece and takes a few minutes.")
        garment_image = clean_image(self.store.download(catalog_paths["imagePath"], MAX_IMAGE_BYTES), folder / "garment.png")
        graph = patch_styled_graph(self.styled_template, base_image=self.comfy.upload_image(base),
                                   garment_image=self.comfy.upload_image(garment_image), key=key)
        guard()
        prompt_id = self.comfy.submit(graph, "styled-" + key)
        result = self.comfy.wait_generation(prompt_id, guard, lambda: None)
        image, size = clean_output(self.comfy.output(result, "24"))
        image2k, size2k = clean_output(self.comfy.output(result, "35"))
        if size2k != (size[0] * 2, size[1] * 2):
            raise JobError("The garment render returned unexpected dimensions.")
        directory.mkdir(parents=True, exist_ok=True)
        for path, data in zip(outputs, (image, image2k)):
            temporary = path.with_name(".styled-" + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        base_file.write_text(base_key)
        return key, outputs, base_key

    def prewarm(self, body_template_ids=None, garment_ids=None, *, warm_swapper=True, strict=False, notify_queued=False):
        """Prepare catalog variants and pose upscales once, without creating personal bases."""
        if body_template_ids is not None:
            body_template_ids = tuple(dict.fromkeys(body_template_ids))
            if not body_template_ids or any(value not in ALL_BODY_TEMPLATE_IDS for value in body_template_ids):
                raise JobError("Choose one or more configured body template IDs.")
        requested_garments = set(garment_ids) if garment_ids else None
        if requested_garments:
            for garment_id in requested_garments:
                safe_id(garment_id)
        rendered, failures, found = [], [], set()
        pose_count, prepared_count = 0, 0
        catalog = [(garment_id, garment) for garment_id, garment in self.store.active_garments()
                   if not requested_garments or garment_id in requested_garments]
        total = sum(len(body_template_ids or configured_body_templates(garment)) for _, garment in catalog)
        reported_at = float("-inf")

        def report_preparation(*, finished=False):
            nonlocal reported_at
            if not notify_queued:
                return
            stamp = time.monotonic()
            if not finished and stamp - reported_at < QUEUED_PROGRESS_INTERVAL:
                return
            reported_at = stamp
            try:
                self.store.queued_enrollment_progress(None if finished and not failures else {"completed": prepared_count, "total": total},
                                                     unavailable=bool(finished and failures))
            except Exception:
                # Status delivery is best effort; it must never retry or cancel accepted GPU work.
                logging.exception("Could not report startup preparation to queued enrollment jobs.")

        report_preparation()
        for garment_id, garment in catalog:
            report_preparation()
            found.add(garment_id)
            variants = body_template_ids or configured_body_templates(garment)
            for body_id in variants:
                try:
                    catalog_paths = validate_garment(garment_id, garment, body_id)
                    if not self.comfy.idle():
                        raise JobError("ComfyUI is busy. Wait for its current work before preparing catalog variants.")
                    with tempfile.TemporaryDirectory(prefix="prewarm-", dir=self.state_dir) as folder:
                        started = time.monotonic()
                        entry = self.styled_garment(garment_id, garment, catalog_paths, Path(folder), report_preparation,
                                                   lambda message, g=garment_id: logging.info("%s: %s", g, message))
                        rendered.append(entry)
                        if not self.comfy.idle():
                            raise JobError("ComfyUI became busy before pose preparation. Run preparation again when idle.")
                        pose_count += int(self.prepare_pose(entry[2], report_preparation))
                        # Masks also stay on disk so the first live scan does not parse the garment.
                        self.garment_masks(entry[0], entry[1], entry[2])
                        prepared_count += 1
                        report_preparation()
                        logging.info("Garment %s / %s ready in %.1f s.", garment_id, body_id or "legacy", time.monotonic() - started)
                except Exception:
                    failures.append({"garmentId": garment_id, "bodyTemplateId": body_id})
                    logging.exception("Could not prepare garment %s / %s.", garment_id, body_id or "legacy")
        if requested_garments:
            failures.extend({"garmentId": garment_id, "bodyTemplateId": None}
                            for garment_id in sorted(requested_garments - found))
        if rendered and warm_swapper:
            # The base model's own face is a harmless source that loads the detector/swapper sessions.
            key, outputs, _ = rendered[0]
            names = self.comfy_styled_inputs(key, outputs)
            graph = patch_swap_graph(self.swap_template, selfie_image=names[0], styled_image=names[0],
                                     styled_2k_image=names[1], job_id="prewarm-" + uuid.uuid4().hex[:12])
            self.comfy.wait_generation(self.comfy.submit(graph, "prewarm"), report_preparation, lambda: None, timeout=300, interval=0.2)
            logging.info("Face swapper warmed; %d garment(s) ready.", len(rendered))
        report = {"variantsPrepared": prepared_count,
                  "posesPrepared": pose_count, "failures": failures}
        report_preparation(finished=True)
        if strict and failures:
            raise JobError(f"Catalog preparation failed for {len(failures)} variant(s); inspect the worker log before onboarding.")
        return report

    def comfy_styled_inputs(self, key, outputs):
        if key not in self.comfy_inputs:
            self.comfy_inputs[key] = tuple(self.comfy.upload_image(path) for path in outputs)
        return self.comfy_inputs[key]

    def generate(self, job_id, job, uid, user, folder, lease):
        if self.pipeline == "qwen":
            return self.generate_qwen(job_id, job, uid, user, folder, lease)
        garment = self.store.garment(job["garmentId"])
        identity = user.get("identity")
        body_id = saved_body_template(identity) if isinstance(identity, dict) and identity.get("mode") == "personal_base" else None
        catalog_paths = validate_garment(job["garmentId"], garment, body_id)
        history = {"uid": uid, "jobId": job_id, "garmentId": job["garmentId"],
                   "garmentName": str(garment.get("name", "Garment"))[:160], "status": "running", "createdAt": now()}
        if body_id is not None:
            history["bodyTemplateId"] = body_id
        if isinstance(identity, dict) and identity.get("status") == "ready" and identity.get("mode") == "personal_base":
            return self.generate_personal(job_id, job, uid, identity, folder, lease, garment, catalog_paths, history)
        # Earlier face-swap and trained identities: HyperSwap from their saved selfie.
        selfie = self.identity_reference(uid, identity, folder)
        key, styled, _ = self.styled_garment(job["garmentId"], garment, catalog_paths, folder, lease.guard,
                                          lambda message: lease.write({"stage": "styling", "message": message, "progress": 0.1},
                                                                      generation=history))
        styled_names = self.comfy_styled_inputs(key, styled)
        graph = patch_swap_graph(self.swap_template, selfie_image=self.comfy.upload_image(selfie),
                                 styled_image=styled_names[0], styled_2k_image=styled_names[1], job_id=job_id)
        # One write both records history and the durable marker before the non-retried POST.
        lease.write({"stage": "generating", "message": "Putting you in the look.", "progress": 0.5,
                     "submissionPending": True}, generation=history)
        prompt_id = self.comfy.submit(graph, job_id)
        result = self.comfy.wait_generation(prompt_id, lease.guard, lambda: None, timeout=300, interval=0.2)
        image, size = clean_output(self.comfy.output(result, "6"))
        image2k, size2k = clean_output(self.comfy.output(result, "7"), image_format="JPEG")
        if size2k != (size[0] * 2, size[1] * 2):
            raise JobError("The face swap returned unexpected dimensions.")
        prefix = f"users/{uid}/generations/{job_id}"
        image_path, image2k_path = prefix + "/result.png", prefix + "/result-2k.jpg"
        lease.guard()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for upload in [pool.submit(self.store.upload, image_path, image, "image/png"),
                           pool.submit(self.store.upload, image2k_path, image2k, "image/jpeg")]:
                upload.result()
        lease.write({"status": "completed", "stage": "ready", "message": "Your try-on is ready.", "progress": 1.0,
                     "comfyPromptId": prompt_id, "submissionPending": False},
                    generation=history | {"status": "completed", "imagePath": image_path, "image2kPath": image2k_path,
                                          "width": size[0], "height": size[1], "width2k": size2k[0], "height2k": size2k[1], "error": None})

    def generate_personal(self, job_id, job, uid, identity, folder, lease, garment, catalog_paths, history):
        """CPU try-on: the garment region of the cached render onto the person's personal base."""
        safe_id(identity.get("version"))
        key, styled, base_key = self.styled_garment(
            job["garmentId"], garment, catalog_paths, folder, lease.guard,
            lambda message: lease.write({"stage": "styling", "message": message, "progress": 0.1}, generation=history),
            allow_render="bodyTemplate" not in identity)
        lease.write({"stage": "generating", "message": "Putting the piece on you.", "progress": 0.4}, generation=history)
        try:
            personal = self.personal_assets(uid, identity, base_key)
            masks = self.garment_masks(key, styled, base_key)
            composite, composite2k = self.composite_tryon(personal, styled, masks, base_key)
        except CompositeError as error:
            raise JobError(str(error)) from None
        except ValueError:
            raise JobError("This piece isn't ready for try-ons yet. Please try another piece.") from None
        output = io.BytesIO()
        composite.save(output, format="PNG")
        image, size = clean_output(output.getvalue())
        output = io.BytesIO()
        composite2k.save(output, format="PNG")
        image2k, size2k = clean_output(output.getvalue(), image_format="JPEG")
        prefix = f"users/{uid}/generations/{job_id}"
        image_path, image2k_path = prefix + "/result.png", prefix + "/result-2k.jpg"
        lease.guard()
        with ThreadPoolExecutor(max_workers=2) as pool:
            for upload in [pool.submit(self.store.upload, image_path, image, "image/png"),
                           pool.submit(self.store.upload, image2k_path, image2k, "image/jpeg")]:
                upload.result()
        lease.write({"status": "completed", "stage": "ready", "message": "Your try-on is ready.", "progress": 1.0},
                    generation=history | {"status": "completed", "imagePath": image_path, "image2kPath": image2k_path,
                                          "width": size[0], "height": size[1], "width2k": size2k[0], "height2k": size2k[1], "error": None})

    def generate_qwen(self, job_id, job, uid, user, folder, lease):
        """Earlier full-diffusion path with a trained personal adapter (minutes per image)."""
        garment = self.store.garment(job["garmentId"])
        catalog_paths = validate_garment(job["garmentId"], garment)
        history = {"uid": uid, "jobId": job_id, "garmentId": job["garmentId"],
                   "garmentName": str(garment.get("name", "Garment"))[:160], "status": "running", "createdAt": now()}
        lease.write({"stage": "preparing", "message": "Loading your identity and garment.", "progress": 0.05}, generation=history)
        profile_id = self.identity_profile(uid, user.get("identity"), folder, lease)
        base = clean_image(self.store.download(catalog_paths["baseImagePath"], MAX_IMAGE_BYTES), folder / "base.png")
        garment_image = clean_image(self.store.download(catalog_paths["imagePath"], MAX_IMAGE_BYTES), folder / "garment.png")
        lease.write({"stage": "generating", "message": "Generating your try-on and 4K image.", "progress": 0.15})
        base_name, garment_name = self.comfy.upload_image(base), self.comfy.upload_image(garment_image)
        graph = patch_graph(self.template, profile_id=profile_id, base_image=base_name, garment_image=garment_name, job_id=job_id)
        lease.write({"submissionPending": True})  # Durable marker before the non-retried POST.
        lease.guard()
        prompt_id = self.comfy.submit(graph, job_id)
        lease.write({"comfyPromptId": prompt_id, "submissionPending": False})
        result = self.comfy.wait_generation(prompt_id, lease.guard, lambda: None)
        image, size = clean_output(self.comfy.output(result, "24"))
        image4k, size4k = clean_output(self.comfy.output(result, "35"))
        if size4k != (size[0] * 4, size[1] * 4):
            raise JobError("The upscaler returned unexpected dimensions.")
        prefix = f"users/{uid}/generations/{job_id}"
        image_path, image4k_path = prefix + "/result.png", prefix + "/result-4k.png"
        lease.write({"stage": "saving", "message": "Saving your private try-on images.", "progress": 0.95})
        self.store.upload(image_path, image, "image/png")
        lease.guard()
        self.store.upload(image4k_path, image4k, "image/png")
        lease.write({"status": "completed", "stage": "ready", "message": "Your try-on is ready.", "progress": 1.0},
                    generation=history | {"status": "completed", "imagePath": image_path, "image4kPath": image4k_path,
                                          "width": size[0], "height": size[1], "width4k": size4k[0], "height4k": size4k[1], "error": None})


def check_readiness(store, comfy, profiles_root, template, identity_config=None, styled_template=None, swap_template=None,
                    personal_template=None):
    """Read-only checks; never claim jobs, write profiles, load a model or submit GPU work."""
    report = {"ready": False, "checks": {}, "warnings": []}
    # Validate the worker's fixed graph mappings using harmless placeholder data.
    patch_graph(template, profile_id="readiness-check", base_image="check_base.png",
                garment_image="check_garment.png", job_id="readiness-check")
    templates = [template]
    if styled_template is not None:
        patch_styled_graph(styled_template, base_image="check_base.png", garment_image="check_garment.png", key="readiness-check")
        templates.append(styled_template)
    if swap_template is not None:
        patch_swap_graph(swap_template, selfie_image="check_selfie.png", styled_image="check_styled.png",
                         styled_2k_image="check_styled_2k.png", job_id="readiness-check")
        templates.append(swap_template)
    if personal_template is not None:
        patch_personal_graph(personal_template, base_image="check_base.png", profile_id="readiness-check",
                             job_id="readiness-check", steps=24, base_outputs=True)
        templates.append(personal_template)
        if not PARSING_MODEL.is_file():
            raise JobError("The human-parsing model is missing from comfy-identity/parsing-assets/.")
    nodes = [node for graph in templates for node in graph.values()]
    profiles_root = Path(profiles_root).resolve()
    if not profiles_root.is_dir() or not os.access(profiles_root, os.R_OK):
        raise JobError("The configured profiles directory does not exist or is not readable.")
    report["checks"]["profileDirectory"] = "readable"
    if identity_config is not None:
        try:
            config = json.loads(Path(identity_config).read_text(encoding="utf-8-sig"))
            training = Path(config["training_root"])
            configured_profiles = Path(config["profiles_root"])
            if (not training.is_absolute() or not configured_profiles.is_absolute()
                    or configured_profiles.resolve() != profiles_root
                    or profiles_root == training.resolve() or not profiles_root.is_relative_to(training.resolve())):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise JobError("Identity config is invalid or its profiles_root differs from --profiles-root.") from None
        prerequisites = (".venv/Scripts/python.exe", "trainer/train_identity_qwen21.py", "trainer/nf4.json",
                         "models/Qwen-Image-2.1/model_index.json")
        if any(not (training / name).is_file() for name in prerequisites):
            raise JobError("The configured isolated trainer is missing a required Python, script, NF4 config or model index file.")
        report["checks"]["identityConfig"] = "profile directory matches; trainer prerequisite files present"
    else:
        report["warnings"].append("Pass --identity-config to verify the installed extension's profile directory and trainer prerequisites.")
    schemas = comfy.json("GET", "/object_info")
    missing_nodes = sorted({node["class_type"] for node in nodes} - set(schemas))
    if missing_nodes:
        raise JobError("ComfyUI is missing workflow node types: " + ", ".join(missing_nodes))
    model_keys = {"unet_name", "clip_name", "vae_name", "lora_name", "bg_removal_name", "model_name"}
    checked_models = 0
    for node in nodes:
        inputs = schemas[node["class_type"]].get("input", {})
        definitions = inputs.get("required", {}) | inputs.get("optional", {})
        for key, value in node["inputs"].items():
            if key not in model_keys:
                continue
            specification = definitions.get(key, [])
            choices = specification[0] if specification and isinstance(specification[0], list) else None
            if choices is None and len(specification) > 1 and isinstance(specification[1], dict):
                choices = specification[1].get("options")
            if not isinstance(choices, list) or value not in choices:
                raise JobError(f"ComfyUI does not list the configured {node['class_type']} model for {key}.")
            checked_models += 1
    report["checks"]["workflow"] = {"graphs": len(templates), "nodeCount": len(nodes), "modelSelectionsAvailable": checked_models}
    # Only the boolean is exposed; profile/job details and queue prompts stay local.
    report["checks"]["comfyIdle"] = comfy.idle()
    if not report["checks"]["comfyIdle"]:
        report["warnings"].append("ComfyUI is busy; the worker will wait before accepting another job.")
    try:
        catalog = list(store.db.collection("garments").where(filter=store.filter("active", "==", True)).limit(1).stream())
        # Object-scoped worker IAM does not grant storage.buckets.get.
        list(store.bucket.list_blobs(prefix="garments/", max_results=1))
    except Exception as error:
        raise JobError(f"Firebase read check failed ({type(error).__name__}). Check ADC, project/bucket, billing and permissions.") from None
    report["checks"]["firestore"] = "read succeeded"
    report["checks"]["storage"] = "bounded catalog object listing succeeded"
    report["checks"]["activeCatalogEntryFound"] = bool(catalog)
    if not catalog:
        report["warnings"].append("No active garment was found. Seed the catalog before generating try-ons.")
    report["warnings"].append("Read checks do not verify write permissions, client security rules, full model integrity or GPU execution.")
    report["ready"] = True
    return report


@contextmanager
def process_lock(path):
    """OS-held lock releases after a crash; two worker processes cannot share this GPU."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("FIREBASE_PROJECT_ID"))
    parser.add_argument("--bucket", default=os.environ.get("FIREBASE_STORAGE_BUCKET"))
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--profiles-root", required=True, type=Path)
    parser.add_argument("--workflow", type=Path, default=ROOT / "comfy-identity/Qwen21_Universal_TryOn_4K.api.json")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local/firebase-worker")
    parser.add_argument("--once", action="store_true", help="Handle at most one available job, then exit.")
    parser.add_argument("--check", action="store_true", help="Read-only Firebase/Comfy/config check; do not consume jobs or start GPU work.")
    parser.add_argument("--identity-config", type=Path, help="For --check: installed universal_identity/config.json to verify profile/trainer configuration.")
    parser.add_argument("--pipeline", choices=("faceswap", "qwen"), default="faceswap",
                        help="faceswap (default): cached garment render + selfie face swap in seconds. qwen: earlier trained-adapter diffusion path.")
    parser.add_argument("--no-prewarm", action="store_true", help="Skip rendering uncached active garments and warming the face swapper at startup.")
    parser.add_argument("--prewarm-only", action="store_true", help="Prepare garment/body variants and fixed-pose upscales, then exit without consuming user jobs.")
    parser.add_argument("--body-template", action="append", choices=ALL_BODY_TEMPLATE_IDS,
                        help="With --prewarm-only: prepare only this body template; repeat to choose several.")
    parser.add_argument("--garment", action="append", help="With --prewarm-only: prepare only this active garment ID; repeat to choose several.")
    args = parser.parse_args(argv)
    if not args.project or not args.bucket:
        parser.error("Set FIREBASE_PROJECT_ID and FIREBASE_STORAGE_BUCKET, or pass --project and --bucket.")
    if args.prewarm_only and (args.check or args.once or args.no_prewarm or args.pipeline != "faceswap"):
        parser.error("--prewarm-only cannot be combined with --check, --once, --no-prewarm, or --pipeline qwen.")
    if (args.body_template or args.garment) and not args.prewarm_only:
        parser.error("--body-template and --garment require --prewarm-only.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.check:
        try:
            template = json.loads(args.workflow.read_text(encoding="utf-8-sig"))
            comfy = ComfyClient(args.comfy_url)
            store = FirebaseStore(args.project, args.bucket)
            result = check_readiness(store, comfy, args.profiles_root, template, args.identity_config,
                                     json.loads(STYLED_TEMPLATE.read_text(encoding="utf-8-sig")),
                                     json.loads(SWAP_TEMPLATE.read_text(encoding="utf-8-sig")),
                                     json.loads(PERSONAL_TEMPLATE.read_text(encoding="utf-8-sig")))
            print(json.dumps({"project": args.project, "bucket": args.bucket, **result}, indent=2))
            return
        except Exception as error:
            message = str(error) if isinstance(error, (JobError, ComfyError)) else f"Readiness check failed ({type(error).__name__}); check ADC and local configuration."
            print(json.dumps({"ready": False, "error": message}, indent=2))
            raise SystemExit(1) from None
    template = json.loads(args.workflow.read_text(encoding="utf-8-sig"))
    comfy = ComfyClient(args.comfy_url)
    worker_id = "worker-" + uuid.uuid4().hex
    with process_lock(args.state_dir / "gpu.lock"):
        store = FirebaseStore(args.project, args.bucket)
        worker = Worker(store, comfy, LocalProfiles(args.profiles_root), template, args.state_dir, pipeline=args.pipeline)
        if args.prewarm_only:
            if not comfy.idle():
                raise JobError("ComfyUI must be idle before catalog preparation. No user jobs were consumed.")
            report = worker.prewarm(args.body_template, args.garment, warm_swapper=False, strict=True)
            print(json.dumps(report, indent=2))
            return
        if args.pipeline == "faceswap" and not args.no_prewarm:
            try:
                worker.prewarm(notify_queued=True)
            except Exception:
                logging.exception("Prewarm failed; new body-template looks require prepared variants. Run --prewarm-only before onboarding.")
        # Two lanes: GPU work (onboarding/training) and CPU try-on compositing, so a scan never
        # waits behind someone else's onboarding. The qwen pipeline renders try-ons on the GPU.
        gpu_kinds = GPU_KINDS | (CPU_KINDS if args.pipeline == "qwen" else frozenset())
        stop = threading.Event()

        def cpu_lane():
            while not stop.is_set():
                try:
                    claimed = store.claim(worker_id + "-cpu", CPU_KINDS)
                    if claimed:
                        worker.run_job(*claimed)
                        continue
                except Exception:
                    logging.exception("CPU lane polling failed; no job is resubmitted automatically.")
                stop.wait(POLL_SECONDS)

        if args.pipeline == "faceswap" and not args.once:
            threading.Thread(target=cpu_lane, name="cpu-lane", daemon=True).start()
        last_expiry = 0.0
        while True:
            try:
                if time.monotonic() - last_expiry >= 30:
                    store.expire_abandoned()
                    last_expiry = time.monotonic()
                # Existing local training or user-queued inference must finish first.
                claimed = store.claim(worker_id, None if args.once else gpu_kinds) if comfy.idle() else None
                if claimed:
                    worker.run_job(*claimed)
                if args.once:
                    break
            except KeyboardInterrupt:
                break
            except Exception:
                logging.exception("Worker polling failed; no job is resubmitted automatically.")
                if args.once:
                    raise
            time.sleep(POLL_SECONDS)
        stop.set()


if __name__ == "__main__":
    main()
