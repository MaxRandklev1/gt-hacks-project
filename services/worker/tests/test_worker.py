import copy
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from services.worker.comfy import LocalProfiles
from services.worker.worker import (FirebaseStore, JobError, Lease, LostLease, Worker, clean_output, failed_identity,
                                    identity_paths, now, sha256, validate_garment, validate_job)


def png(size=(16, 16), metadata=False, color="navy"):
    buffer = io.BytesIO()
    info = PngInfo()
    extra = {}
    if metadata:
        info.add_text("prompt", "private workflow")
        exif = Image.Exif()
        exif[315] = "private image author"
        extra["exif"] = exif
    Image.new("RGB", size, color).save(buffer, format="PNG", pnginfo=info, **extra)
    return buffer.getvalue()


class Snapshot:
    def __init__(self, reference):
        self.reference, self.id = reference, reference.path.split("/")[-1]
    def to_dict(self):
        return copy.deepcopy(self.reference.db.data.get(self.reference.path))


class Reference:
    def __init__(self, db, path):
        self.db, self.path = db, path
    def get(self, **_):
        return Snapshot(self)
    def collection(self, name):
        return Collection(self.db, self.path + "/" + name)


class Collection:
    def __init__(self, db, path):
        self.db, self.path = db, path
    def document(self, name):
        return Reference(self.db, self.path + "/" + name)
    def where(self, *, filter):
        self.query = filter
        return self
    def limit(self, count):
        return self
    def stream(self):
        key, _, value = self.query
        return [Snapshot(Reference(self.db, path)) for path, data in list(self.db.data.items())
                if path.startswith(self.path + "/") and path.count("/") == self.path.count("/") + 1
                and data.get(key) == value]


class Transaction:
    def update(self, reference, data):
        reference.db.data[reference.path].update(copy.deepcopy(data))
    def set(self, reference, data, merge=False):
        reference.db.data.setdefault(reference.path, {}).update(copy.deepcopy(data))


class FakeDB:
    def __init__(self, data):
        self.data = data
    def collection(self, name):
        return Collection(self, name)


def fake_store(data):
    store = FirebaseStore.__new__(FirebaseStore)
    store.db = FakeDB(data)
    store.filter = lambda *args: args
    store.transaction = lambda callback: callback(Transaction())
    return store


class WorkerTests(unittest.TestCase):
    def test_version3_selfie_source_and_selection_timestamp_contract(self):
        stamp, upload = now(), str(uuid.uuid4())
        job = {"uid": "alice", "kind": "train", "requestVersion": 3, "uploadId": upload,
               "selfiePath": f"users/alice/uploads/{upload}/selfie.jpg", "selfieSource": "upload",
               "selfieSelectedAt": stamp, "createdAt": stamp,
               "photoPaths": [f"users/alice/uploads/{upload}/{i}.jpg" for i in range(8)]}
        self.assertEqual(validate_job("job1", job), "alice")
        camera = job | {"selfieSource": "camera", "selfieCapturedAt": stamp}
        self.assertEqual(validate_job("job1", camera), "alice")
        for altered in [job | {"selfieSource": "gallery"}, job | {"selfieSelectedAt": None},
                        job | {"selfieSelectedAt": stamp - timedelta(hours=2)},
                        job | {"selfieSelectedAt": stamp + timedelta(minutes=3)},
                        job | {"selfieCapturedAt": stamp}, job | {"selfieCapturedAt": None},
                        job | {"selfieSource": "camera"}, camera | {"selfieCapturedAt": stamp + timedelta(seconds=1)},
                        camera | {"selfieCapturedAt": stamp - timedelta(hours=2)},
                        camera | {"selfieCapturedAt": stamp.replace(tzinfo=None)}]:
            with self.subTest(altered=altered), self.assertRaises(JobError):
                validate_job("job1", altered)
        # Queue delays cannot make a valid submission stale; choice time is not image age.
        old = stamp - timedelta(days=2)
        self.assertEqual(validate_job("job1", job | {"createdAt": old, "selfieSelectedAt": old}), "alice")
        for version in (1, 2, 3):
            self.assertEqual(validate_job("job1", {"uid": "alice", "kind": "generate", "requestVersion": version,
                                                   "garmentId": "shirt"}), "alice")

    def test_training_requires_exactly_eight_owner_paths(self):
        upload = str(uuid.uuid4())
        job = {"uid": "alice", "kind": "train", "requestVersion": 2, "uploadId": upload,
               "selfiePath": f"users/alice/uploads/{upload}/selfie.jpg", "selfieCapturedAt": now(), "createdAt": now(),
               "photoPaths": [f"users/alice/uploads/{upload}/{i}.jpg" for i in range(8)]}
        self.assertEqual(validate_job("job1", job), "alice")
        for altered in [job | {"photoPaths": job["photoPaths"][:7]},
                        job | {"photoPaths": [p.replace("alice", "bob") for p in job["photoPaths"]]},
                        job | {"photoPaths": job["photoPaths"][::-1]}, job | {"uid": "alice/../bob"},
                        job | {"requestVersion": True}]:
            with self.assertRaises(ValueError):
                validate_job("job1", altered)
        for altered in [job | {"requestVersion": 1}, job | {"selfiePath": None},
                        job | {"selfiePath": job["selfiePath"].replace("alice", "bob")},
                        job | {"selfieCapturedAt": None}, job | {"selfieCapturedAt": now() - timedelta(hours=2)},
                        job | {"selfieCapturedAt": now() + timedelta(minutes=3)}]:
            with self.assertRaises(JobError):
                validate_job("job1", altered)
        # A long queue delay does not change the capture window relative to submission.
        old = now() - timedelta(days=1)
        self.assertEqual(validate_job("job1", job | {"createdAt": old, "selfieCapturedAt": old}), "alice")

    def test_catalog_rejects_owner_input_or_external_path(self):
        valid = {"active": True, "imagePath": "garments/shirt/reference.png", "baseImagePath": "garments/shirt/base.png"}
        self.assertEqual(validate_garment("shirt", valid)["imagePath"], valid["imagePath"])
        for value in [valid | {"active": False}, valid | {"imagePath": "users/bob/photo.png"},
                      valid | {"baseImagePath": "https://external.test/base.png"}]:
            with self.assertRaises(JobError):
                validate_garment("shirt", value)

    def test_claim_does_not_adopt_running_jobs_and_lease_fences_writes(self):
        store = fake_store({"jobs/a": {"uid": "alice", "status": "queued", "kind": "generate",
                                      "requestVersion": 1, "garmentId": "shirt"}, "users/alice": {}})
        job_id, job = store.claim("worker-a")
        self.assertEqual(job_id, "a")
        self.assertIsNone(store.claim("worker-b"))
        with self.assertRaises(LostLease):
            store.update("a", "worker-b", job["leaseToken"], {"status": "completed"})
        with self.assertRaises(LostLease):
            store.update("a", "worker-a", "wrong-token", {"status": "completed"})
        store.db.data["jobs/a"]["leaseExpiresAt"] = now() - timedelta(seconds=1)
        with self.assertRaises(LostLease):
            store.update("a", "worker-a", job["leaseToken"], {"status": "completed"})
        store.expire_abandoned()
        self.assertEqual(store.db.data["jobs/a"]["status"], "failed")
        self.assertIsNone(store.claim("worker-b"))

    def test_terminal_history_is_owner_scoped(self):
        store = fake_store({"jobs/a": {"uid": "alice", "status": "queued", "kind": "generate",
                                      "requestVersion": 1, "garmentId": "shirt"}, "users/alice": {}})
        _, job = store.claim("worker-a")
        store.update("a", "worker-a", job["leaseToken"], {"status": "completed"},
                     generation={"status": "completed", "imagePath": "users/alice/generations/a/result.png"})
        self.assertIn("users/alice/generations/a", store.db.data)
        self.assertFalse(any(path.startswith("users/bob") for path in store.db.data))
        with self.assertRaises(LostLease):
            store.update("a", "worker-a", job["leaseToken"], {"status": "running"})

    def test_bad_request_is_failed_without_claim_and_expired_local_guard_blocks(self):
        store = fake_store({"jobs/a": {"uid": "../bob", "status": "queued", "kind": "generate",
                                      "requestVersion": 1, "garmentId": "shirt"}})
        self.assertIsNone(store.claim("worker-a"))
        self.assertEqual(store.db.data["jobs/a"]["status"], "failed")
        lease = Lease(Mock(), "job1", {"leaseOwner": "a", "leaseToken": "token",
                                       "leaseExpiresAt": now() - timedelta(seconds=1)})
        with self.assertRaises(LostLease):
            lease.guard()

    def test_output_png_strips_prompt_metadata(self):
        output, size = clean_output(png(metadata=True))
        self.assertEqual(size, (16, 16))
        self.assertFalse(Image.open(io.BytesIO(output)).info)

    def test_retraining_failure_and_expired_lease_preserve_previous_owner_identity(self):
        old = {"status": "ready", "version": "old1", "profileId": "old-local", **identity_paths("alice", "old1")}
        self.assertEqual(failed_identity("alice", "new1", "failed", old)["version"], "old1")
        self.assertEqual(failed_identity("bob", "new1", "failed", old)["status"], "failed")
        store = fake_store({"jobs/new1": {"uid": "alice", "kind": "train", "status": "running",
                            "leaseExpiresAt": now() - timedelta(seconds=1), "previousIdentity": old},
                            "users/alice": {"trainingJobId": "new1", "identity": {"status": "training"}}})
        store.expire_abandoned()
        self.assertEqual(store.db.data["jobs/new1"]["status"], "failed")
        restored = store.db.data["users/alice"]["identity"]
        self.assertEqual(restored["status"], "ready")
        self.assertEqual(restored["adapterPath"], old["adapterPath"])
        self.assertIn("lastTrainingError", restored)

    def test_initial_user_read_failure_does_not_replace_an_unread_identity(self):
        upload = str(uuid.uuid4())
        job = {"uid": "alice", "kind": "train", "requestVersion": 2, "uploadId": upload,
               "selfiePath": f"users/alice/uploads/{upload}/selfie.jpg", "selfieCapturedAt": now(), "createdAt": now(),
               "photoPaths": [f"users/alice/uploads/{upload}/{i}.jpg" for i in range(8)]}
        store, comfy, lease = Mock(), Mock(), Mock()
        store.user.side_effect = RuntimeError("transient Firestore read failure")
        with tempfile.TemporaryDirectory() as temp, patch("services.worker.worker.Lease") as lease_class:
            lease_class.return_value.__enter__.return_value = lease
            worker = Worker(store, comfy, Mock(), {}, temp)
            with self.assertLogs(level="ERROR"):
                worker.run_job("train1", job)
        self.assertEqual(lease.write.call_args.args[0]["status"], "failed")
        self.assertNotIn("user_fields", lease.write.call_args.kwargs)
        comfy.train.assert_not_called()

    def test_foreign_manifest_fails_before_local_profile_or_gpu(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = identity_paths("alice", "train1")
            manifest = {"schemaVersion": 1, "uid": "bob", "version": "train1", "steps": 400, **paths}
            store, comfy, profiles, lease = Mock(), Mock(), Mock(), Mock()
            store.download.return_value = json.dumps(manifest).encode()
            worker = Worker(store, comfy, profiles, {}, temp, selector=Mock())
            with self.assertRaises(JobError):
                worker.identity_profile("alice", {"status": "ready", "version": "train1", **paths}, Path(temp), lease)
            profiles.adapter.assert_not_called()
            comfy.create_profile.assert_not_called()
            comfy.submit.assert_not_called()

    def test_missing_profile_restores_original_token_and_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            profiles = LocalProfiles(folder / "profiles")
            paths = identity_paths("alice", "train1")
            reference, adapter = png(), b"fake-safetensors"
            manifest = {"schemaVersion": 1, "uid": "alice", "version": "train1", "steps": 400,
                        "trigger": "trained_person", **paths,
                        "adapterSha256": sha256(adapter), "referenceSha256": sha256(reference)}
            payloads = {paths["manifestPath"]: json.dumps(manifest).encode(), paths["adapterPath"]: adapter,
                        paths["referencePath"]: reference}
            store, comfy, lease = Mock(), Mock(), Mock()
            store.download.side_effect = lambda path, limit: payloads[path]
            def create(*_):
                directory = profiles.root / "restored-profile"
                directory.mkdir(parents=True)
                (directory / "profile.json").write_text(json.dumps({"id": "restored-profile", "trigger": "new_token",
                    "photos": [{"id": "p0000", "caption": "new caption"}]}))
                return {"id": "restored-profile"}
            comfy.create_profile.side_effect = create
            worker = Worker(store, comfy, profiles, {}, folder / "state", selector=Mock())
            result = worker.identity_profile("alice", {"status": "ready", "version": "train1",
                                             "profileId": "missing-profile", **paths}, folder, lease)
            self.assertEqual(result, "restored-profile")
            restored, profile = profiles.adapter(result, "alice", "train1")
            self.assertEqual(restored.read_bytes(), adapter)
            self.assertEqual(profile["trigger"], "trained_person")
            comfy.submit.assert_not_called()

    def test_training_selects_five_and_publishes_owner_manifest(self):
        self.check_training_reference("camera")

    def test_uploaded_selfie_is_reference_without_becoming_training_data(self):
        self.check_training_reference("upload")

    def check_training_reference(self, source):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            adapter = folder / "fake.safetensors"
            adapter.write_bytes(b"fake-weights")
            store, comfy, profiles, lease = Mock(), Mock(), Mock(), Mock()
            selfie = png(color="red")
            store.download.side_effect = lambda path, limit: selfie if path.endswith("selfie.jpg") else png()
            uploaded = {}
            store.upload.side_effect = lambda path, data, content_type: uploaded.update({path: data})
            comfy.create_profile.return_value = {"id": "alice-local", "photos": [{"id": "p0000"}]}
            comfy.train.return_value = {"job_id": "local-training"}
            comfy.photo.return_value = png()
            profiles.adapter.return_value = (adapter, {"trigger": "trained_person"})
            selector = Mock(return_value=[{"index": i, "selected": i < 5, "score": float(8-i),
                                          "reason": "test", "metrics": {}} for i in range(8)])
            job = {"photoPaths": [f"users/alice/uploads/upload/{i}.jpg" for i in range(8)],
                   "selfiePath": "users/alice/uploads/upload/selfie.jpg", "selfieCapturedAt": now()}
            if source == "upload":
                job = {key: value for key, value in job.items() if key != "selfieCapturedAt"}
                job |= {"selfieSource": "upload", "selfieSelectedAt": now()}
            selfie_validator = Mock(return_value={"face_count": 1})
            worker = Worker(store, comfy, profiles, {}, folder / "state", selector=selector, selfie_validator=selfie_validator)
            worker.train("train1", job, "alice", folder, lease)
            self.assertEqual(len(selector.call_args.args[0]), 8)
            self.assertEqual(len(comfy.create_profile.call_args.args[1]), 5)
            self.assertTrue(all(path.read_bytes() != selfie for path in comfy.create_profile.call_args.args[1]))
            self.assertTrue(all(path.name != "selfie-reference.png" for path in selector.call_args.args[0]))
            selfie_validator.assert_called_once()
            comfy.train.assert_called_once_with("alice-local")
            comfy.photo.assert_not_called()
            paths = identity_paths("alice", "train1")
            self.assertEqual(set(uploaded), set(paths.values()))
            manifest = json.loads(uploaded[paths["manifestPath"]])
            self.assertEqual(manifest["uid"], "alice")
            self.assertEqual(manifest["trigger"], "trained_person")
            self.assertEqual(manifest["steps"], 400)
            self.assertEqual(manifest["schemaVersion"], 2)
            reference_source = "recent_selfie" if source == "upload" else "live_selfie"
            self.assertEqual(manifest["referenceSource"], reference_source)
            self.assertEqual(manifest["selfieSelectedAt"], job.get("selfieSelectedAt", job.get("selfieCapturedAt")).isoformat())
            if source == "upload":
                self.assertNotIn("selfieCapturedAt", manifest)
                self.assertNotIn("selfieCapturedAt", lease.write.call_args.kwargs["user_fields"]["identity"])
            self.assertEqual(manifest["referenceSourcePath"], job["selfiePath"])
            self.assertEqual(uploaded[paths["referencePath"]], selfie)
            profiles.bind_reference.assert_called_once_with("alice-local", "alice", "train1", selfie, source=reference_source)
            self.assertEqual(sum(r["selected"] for r in manifest["selectedPhotos"]), 5)
            self.assertTrue(all(r["sourcePath"].startswith("users/alice/") for r in manifest["selectedPhotos"]))
            self.assertEqual(lease.write.call_args.args[0]["status"], "completed")

    def test_bad_or_missing_selfie_stops_before_profile_creation_or_training(self):
        with tempfile.TemporaryDirectory() as temp:
            store, comfy = Mock(), Mock()
            store.download.return_value = png()
            worker = Worker(store, comfy, Mock(), {}, temp, selfie_validator=Mock(side_effect=ValueError("Take or choose a clear recent selfie.")))
            with self.assertRaises(JobError):
                worker.train("job1", {"selfiePath": "users/alice/uploads/x/selfie.jpg"}, "alice", Path(temp), Mock())
            comfy.create_profile.assert_not_called()
            comfy.train.assert_not_called()

    def test_schema2_cached_and_restored_profiles_use_selfie_without_training_photo_fallback(self):
        for cached, source in ((False, "live_selfie"), (True, "live_selfie"), (False, "recent_selfie"), (True, "recent_selfie")):
            with self.subTest(cached=cached, source=source), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                profiles = LocalProfiles(folder / "profiles")
                reference, adapter = png(color="red"), b"fake-weights"
                paths = identity_paths("alice", "train1")
                upload = str(uuid.uuid4())
                manifest = {"schemaVersion": 2, "uid": "alice", "version": "train1", "trigger": "trained_person",
                            "steps": 400, **paths, "referenceSource": source,
                            "referenceSourcePath": f"users/alice/uploads/{upload}/selfie.jpg", "selfieCapturedAt": now().isoformat(),
                            "referenceSha256": sha256(reference), "adapterSha256": sha256(adapter)}
                if source == "recent_selfie":
                    manifest["selfieSelectedAt"] = manifest.pop("selfieCapturedAt")
                def create(*_):
                    directory = profiles.root / "person"
                    directory.mkdir(parents=True)
                    (directory / "profile.json").write_text(json.dumps({"id": "person", "trigger": "new",
                        "photos": [{"id": "p0000", "caption": "training photo", "selected": True}]}))
                    return {"id": "person"}
                store, comfy, lease = Mock(), Mock(), Mock()
                payloads = {paths["manifestPath"]: json.dumps(manifest).encode(), paths["adapterPath"]: adapter, paths["referencePath"]: reference}
                store.download.side_effect = lambda path, limit: payloads[path]
                comfy.create_profile.side_effect = create
                comfy.profile.return_value = {"reference_source": source, "training": {"adapter_available": True}}
                if cached:
                    create()
                    profiles.restore("person", "alice", manifest, adapter, reference_bytes=reference)
                    # Cached training profiles may still have five selected photos; inference must ignore them.
                    directory, profile = profiles.read("person")
                    profile["photos"][0]["selected"] = True
                    profiles.save(directory, profile)
                worker = Worker(store, comfy, profiles, {}, folder / "state")
                result = worker.identity_profile("alice", {"status": "ready", "version": "train1", "profileId": "person", **paths}, folder, lease)
                self.assertEqual(result, "person")
                self.assertEqual(profiles.verify_reference(result, "alice", "train1", sha256(reference), source=source).read_bytes(), reference)
                with self.assertRaises(ValueError):
                    profiles.verify_reference(result, "alice", "train1", sha256(reference),
                                              source="live_selfie" if source == "recent_selfie" else "recent_selfie")
                if cached:
                    comfy.create_profile.assert_not_called()
                    self.assertEqual(store.download.call_count, 1)  # Only manifest; local selfie verified.
                else:
                    self.assertFalse(profiles.read(result)[1]["photos"][0]["selected"])
                comfy.train.assert_not_called()
                comfy.photo.assert_not_called()

    def test_schema2_rejects_false_capture_claim_and_invalid_source_timestamps(self):
        stamp = now().isoformat()
        paths = identity_paths("alice", "train1")
        manifest = {"schemaVersion": 2, "uid": "alice", "version": "train1", "steps": 400, **paths,
                    "referenceSource": "recent_selfie", "selfieSelectedAt": stamp,
                    "referenceSourcePath": f"users/alice/uploads/{uuid.uuid4()}/selfie.jpg"}
        for changed in [{"selfieCapturedAt": stamp}, {"selfieCapturedAt": None}, {"selfieSelectedAt": None},
                        {"selfieSelectedAt": "not-a-timestamp"}, {"selfieSelectedAt": "2026-09-26T10:00:00"},
                        {"referenceSource": "other"}, {"referenceSource": "live_selfie"},
                        {"referenceSource": "live_selfie", "selfieCapturedAt": (now() + timedelta(days=1)).isoformat()}]:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                store, comfy = Mock(), Mock()
                store.download.return_value = json.dumps(manifest | changed).encode()
                worker = Worker(store, comfy, Mock(), {}, temp)
                with self.assertRaises(JobError):
                    worker.identity_profile("alice", {"status": "ready", "version": "train1", **paths}, Path(temp), Mock())
                comfy.create_profile.assert_not_called()

    def test_schema2_cannot_fall_back_when_reference_provenance_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = identity_paths("alice", "train1")
            manifest = {"schemaVersion": 2, "uid": "alice", "version": "train1", "steps": 400, **paths}
            store, comfy = Mock(), Mock()
            store.download.return_value = json.dumps(manifest).encode()
            worker = Worker(store, comfy, Mock(), {}, temp)
            with self.assertRaises(JobError):
                worker.identity_profile("alice", {"status": "ready", "version": "train1", **paths}, Path(temp), Mock())
            comfy.create_profile.assert_not_called()
            comfy.submit.assert_not_called()

    def test_qwen_generation_uses_catalog_and_publishes_two_private_results(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            store, comfy, profiles, lease = Mock(), Mock(), Mock(), Mock()
            store.garment.return_value = {"active": True, "name": "Test shirt",
                                         "imagePath": "garments/shirt/reference.png", "baseImagePath": "garments/shirt/base.png"}
            store.download.return_value = png()
            comfy.upload_image.side_effect = ["cloud_base.png", "cloud_garment.png"]
            comfy.submit.return_value = "prompt1"
            comfy.wait_generation.return_value = {"outputs": {}}
            comfy.output.side_effect = [png((16, 16), metadata=True), png((64, 64), metadata=True)]
            template = json.loads((Path(__file__).resolve().parents[3] / "comfy-identity/Qwen21_Universal_TryOn_4K.api.json").read_text())
            worker = Worker(store, comfy, profiles, template, folder / "state", selector=Mock(), pipeline="qwen")
            worker.identity_profile = Mock(return_value="alice-profile")
            worker.generate("gen1", {"garmentId": "shirt"}, "alice", {"identity": {"status": "ready"}}, folder, lease)
            graph = comfy.submit.call_args.args[0]
            self.assertEqual(graph["14"]["inputs"]["profile_id"], "alice-profile")
            self.assertEqual([call.args[0] for call in store.upload.call_args_list],
                             ["users/alice/generations/gen1/result.png", "users/alice/generations/gen1/result-4k.png"])
            self.assertEqual(lease.write.call_args.kwargs["generation"]["width4k"], 64)
            self.assertEqual(lease.write.call_args.args[0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
