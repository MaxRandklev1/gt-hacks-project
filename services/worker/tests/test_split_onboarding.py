"""Photo-only training and retriable reference finalization without GPU/cloud calls."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from services.worker.comfy import ComfyClient, LocalProfiles
from services.worker.worker import (JobError, Worker, identity_paths, now, pending_manifest_path,
                                    sha256, validate_job, validate_finalize_context)
from services.worker.tests.test_worker import fake_store, png


class SplitOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.stamp = now()
        self.upload = str(uuid.uuid4())
        self.train_job = {"uid": "alice", "kind": "train", "requestVersion": 4, "status": "completed",
                          "createdAt": self.stamp, "uploadId": self.upload,
                          "photoPaths": [f"users/alice/uploads/{self.upload}/{i}.jpg" for i in range(8)]}
        self.draft = {"trainingJobId": "train1", "uploadId": str(uuid.uuid4()), "selfieSource": "upload",
                      "selfieSelectedAt": self.stamp, "submittedAt": self.stamp}
        self.draft["selfiePath"] = f"users/alice/uploads/{self.draft['uploadId']}/selfie.jpg"
        self.adapter = b"trained-adapter"
        self.paths = identity_paths("alice", "train1")
        self.pending_path = pending_manifest_path("alice", "train1")
        self.pending = {"schemaVersion": 3, "referencePending": True, "uid": "alice", "version": "train1",
                        "profileId": "trained", "trigger": "trained_person", "steps": 80,
                        "adapterPath": self.paths["adapterPath"], "adapterSha256": sha256(self.adapter),
                        "selectedPhotos": [{"index": i, "selected": i < 5, "score": 80-i} for i in range(8)]}
        self.identity = {"status": "awaiting_reference", "version": "train1", "profileId": "trained", "steps": 80,
                         "adapterPath": self.paths["adapterPath"], "trainingManifestPath": self.pending_path}
        self.user = {"trainingJobId": "train1", "identity": self.identity, "heightCm": 180, "weightKg": 75,
                     "measurementSystem": "us", "consentVersion": "identity-training-v1", "consentAt": self.stamp}
        self.job = {"uid": "alice", "kind": "finalize", "requestVersion": 4, "trainingJobId": "train1",
                    "status": "running", "createdAt": self.stamp, "leaseOwner": "worker", "leaseToken": "token",
                    "leaseExpiresAt": now() + timedelta(minutes=2)}
        self.store = fake_store({"users/alice": copy.deepcopy(self.user), "jobs/train1": self.train_job,
                                 "users/alice/onboarding/current": copy.deepcopy(self.draft), "jobs/final1": self.job})
        self.objects = {self.pending_path: json.dumps(self.pending).encode(), self.paths["adapterPath"]: self.adapter,
                        self.draft["selfiePath"]: png(color="red")}
        self.store.download = Mock(side_effect=lambda path, limit: self.objects[path])
        def upload(path, data, content_type):
            if path in self.objects and self.objects[path] != data:
                raise JobError("Immutable object has different bytes.")
            self.objects[path] = data
        self.store.upload = Mock(side_effect=upload)
        self.profiles = LocalProfiles(self.folder / "profiles")
        self.comfy = Mock()
        def create(*_):
            profile_id = "restored" if (self.profiles.root / "trained").exists() else "trained"
            directory = self.profiles.root / profile_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "profile.json").write_text(json.dumps({"id": profile_id, "trigger": "new", "photos": []}))
            return {"id": profile_id}
        self.comfy.create_profile.side_effect = create
        def public(profile_id):
            _, profile = self.profiles.read(profile_id)
            return {"training": {"adapter_available": bool(profile.get("latest_successful"))},
                    "reference_source": (profile.get("inference_reference") or {}).get("source")}
        self.comfy.profile.side_effect = public
        self.worker = Worker(self.store, self.comfy, self.profiles, {}, self.folder / "state",
                             selfie_validator=Mock(return_value={"face_count": 1}))
        self.lease = Mock()
        self.lease.write.side_effect = lambda fields=None, **kwargs: self.store.update("final1", "worker", "token", fields, **kwargs)

    def install_cached(self):
        self.comfy.create_profile("trained", [])
        self.profiles.restore("trained", "alice", self.pending, self.adapter)
        self.comfy.create_profile.reset_mock()

    def finish(self):
        self.worker.finalize("final1", self.job, "alice", self.store.user("alice"), self.folder, self.lease)

    def test_v4_photo_only_and_finalize_requests_accept_no_measurements_or_selfie(self):
        self.assertEqual(validate_job("train1", self.train_job), "alice")
        self.assertEqual(validate_job("final1", self.job), "alice")
        for changed in ({"steps": 400}, {"selfiePath": "x"}, {"heightCm": 180}, {"photoPaths": []}):
            with self.subTest(changed=changed), self.assertRaises(JobError):
                validate_job("train1", self.train_job | changed)
        for version in (1, 2, 3, 4):
            self.assertEqual(validate_job("gen1", {"uid": "alice", "kind": "generate", "requestVersion": version, "garmentId": "shirt"}), "alice")

    def test_training_is_80_steps_and_does_not_read_questions_or_selfie(self):
        for questions_already_answered in (False, True):
            with self.subTest(questions_already_answered=questions_already_answered):
                store, comfy, profiles, lease = Mock(), Mock(), Mock(), Mock()
                store.user.return_value = {"consentVersion": "identity-training-v1", "consentAt": self.stamp}
                if questions_already_answered:
                    store.user.return_value.update(heightCm=180, weightKg=75)
                store.download.return_value = png()
                adapter = self.folder / "adapter.safetensors"
                adapter.write_bytes(self.adapter)
                profiles.adapter.return_value = (adapter, {"trigger": "trained_person"})
                comfy.create_profile.return_value = {"id": "trained"}
                comfy.train.return_value = {"job_id": "local1"}
                comfy.wait_training.side_effect = lambda job_id, guard, callback, **kwargs: callback(0.5)
                selector = Mock(return_value=self.pending["selectedPhotos"])
                worker = Worker(store, comfy, profiles, {}, self.folder / "state", selector=selector)
                with patch("services.worker.worker.Lease") as lease_type:
                    lease_type.return_value.__enter__.return_value = lease
                    worker.run_job("train1", self.train_job)
                comfy.train.assert_called_once_with("trained", steps=80)
                self.assertEqual(comfy.wait_training.call_args.kwargs["steps"], 80)
                self.assertEqual(store.download.call_count, 8)
                store.onboarding.assert_not_called()
                profiles.bind_reference.assert_not_called()
                self.assertEqual(len(comfy.create_profile.call_args.args[1]), 5)
                saved = {call.args[0]: call.args[1] for call in store.upload.call_args_list}
                self.assertEqual(set(saved), {self.pending_path, self.paths["adapterPath"]})
                manifest = json.loads(saved[self.pending_path])
                self.assertEqual((manifest["schemaVersion"], manifest["steps"], manifest["referencePending"]), (3, 80, True))
                self.assertNotIn("referencePath", manifest)
                self.assertEqual(lease.write.call_args.kwargs["user_fields"]["identity"]["status"], "awaiting_reference")
                self.assertTrue(any(call.args and "40/80" in call.args[0].get("message", "") for call in lease.write.call_args_list))

    def test_finalization_binds_reference_without_training_and_keeps_80_step_manifest(self):
        self.install_cached()
        self.finish()
        ready = self.store.user("alice")["identity"]
        paths = identity_paths("alice", "train1", self.draft["uploadId"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["referenceVersion"], self.draft["uploadId"])
        self.assertEqual(ready["adapterPath"], self.paths["adapterPath"])
        manifest = json.loads(self.objects[paths["manifestPath"]])
        self.assertEqual((manifest["schemaVersion"], manifest["steps"]), (2, 80))
        self.assertNotIn("referencePending", manifest)
        self.assertEqual(self.profiles.verify_reference("trained", "alice", "train1", manifest["referenceSha256"], source="recent_selfie").read_bytes(), png(color="red"))
        self.comfy.train.assert_not_called()
        self.comfy.create_profile.assert_not_called()

    def test_camera_reference_finalization_records_real_capture_time(self):
        self.store.db.data["users/alice/onboarding/current"].update(selfieSource="camera", selfieCapturedAt=self.stamp)
        self.finish()
        ready = self.store.user("alice")["identity"]
        manifest = json.loads(self.objects[ready["manifestPath"]])
        self.assertEqual(manifest["referenceSource"], "live_selfie")
        self.assertEqual(manifest["selfieCapturedAt"], self.stamp.isoformat())
        self.comfy.train.assert_not_called()

    def test_pending_step_count_survives_a_later_default_change(self):
        self.pending["steps"] = 120
        self.objects[self.pending_path] = json.dumps(self.pending).encode()
        self.store.db.data["users/alice"]["identity"]["steps"] = 120
        self.finish()
        ready = self.store.user("alice")["identity"]
        self.assertEqual(ready["steps"], 120)
        self.assertEqual(json.loads(self.objects[ready["manifestPath"]])["steps"], 120)
        self.assertEqual(self.profiles.read(ready["profileId"])[1]["latest_successful"]["steps"], 120)

    def test_answered_questions_wait_for_parent_then_finalization_works(self):
        self.store.db.data["jobs/train1"]["status"] = "running"
        with self.assertRaises(JobError):
            self.finish()
        self.comfy.train.assert_not_called()
        self.store.db.data["jobs/train1"]["status"] = "completed"
        self.finish()
        self.assertEqual(self.store.user("alice")["identity"]["status"], "ready")

    def test_missing_cache_restores_trained_adapter_without_retraining_or_photo_fallback(self):
        self.finish()
        _, restored = self.profiles.read("trained")
        self.assertEqual(restored["latest_successful"]["steps"], 80)
        self.assertTrue(restored["inference_reference_required"])
        self.assertFalse(any(photo.get("selected") for photo in restored["photos"]))
        self.comfy.train.assert_not_called()
        self.assertEqual(self.profiles.adapter("trained", "alice", "train1")[0].read_bytes(), self.adapter)

    def test_missing_draft_foreign_parent_and_invalid_measurements_fail_before_gpu(self):
        for changed, draft, parent in [({}, None, self.train_job), ({}, self.draft, self.train_job | {"uid": "bob"}),
                                       ({"heightCm": 79}, self.draft, self.train_job), ({"weightKg": 301}, self.draft, self.train_job),
                                       ({"trainingJobId": "other"}, self.draft, self.train_job)]:
            with self.subTest(changed=changed, draft=draft), self.assertRaises(JobError):
                validate_finalize_context("alice", "train1", self.user | changed, parent, draft)
        self.comfy.train.assert_not_called()

    def test_draft_timestamps_checked_against_submission_not_queue_delay(self):
        old = self.stamp - timedelta(days=2)
        draft = self.draft | {"selfieSelectedAt": old, "submittedAt": old}
        validate_finalize_context("alice", "train1", self.user, self.train_job, draft)
        for altered in (draft | {"selfieCapturedAt": old}, draft | {"selfieSelectedAt": old - timedelta(hours=2)},
                        draft | {"selfiePath": self.draft["selfiePath"].replace("alice", "bob")}):
            with self.assertRaises(JobError):
                validate_finalize_context("alice", "train1", self.user, self.train_job, altered)

    def test_atomic_fence_rejects_changed_identity_or_draft(self):
        fence = {"trainingJobId": "train1", "draft": copy.deepcopy(self.draft)}
        for change in ("identity", "draft"):
            with self.subTest(change=change):
                self.store.db.data["users/alice"] = copy.deepcopy(self.user)
                self.store.db.data["users/alice/onboarding/current"] = copy.deepcopy(self.draft)
                if change == "identity":
                    self.store.db.data["users/alice"]["identity"]["version"] = "new-training"
                else:
                    self.store.db.data["users/alice/onboarding/current"]["submittedAt"] += timedelta(seconds=1)
                with self.assertRaises(JobError):
                    self.store.update("final1", "worker", "token", {"status": "completed"},
                                      user_fields={"identity": {"status": "ready"}}, finalization=fence)
                self.assertEqual(self.store.db.data["jobs/final1"]["status"], "running")
                self.assertNotEqual(self.store.user("alice")["identity"].get("status"), "ready")

    def test_bad_selfie_failure_keeps_trained_adapter_for_retry(self):
        self.worker.selfie_validator.side_effect = ValueError("Choose a clearer selfie.")
        with self.assertLogs(level="ERROR"):
            self.worker.run_job("final1", self.job)
        self.assertEqual(self.store.db.data["jobs/final1"]["status"], "failed")
        self.assertEqual(self.store.user("alice")["identity"]["status"], "awaiting_reference")
        self.assertEqual(self.objects[self.paths["adapterPath"]], self.adapter)
        self.comfy.train.assert_not_called()

    def test_expired_finalization_preserves_awaiting_reference_and_adapter(self):
        self.store.db.data["jobs/final1"]["leaseExpiresAt"] = now() - timedelta(seconds=1)
        self.store.expire_abandoned()
        self.assertEqual(self.store.db.data["jobs/final1"]["status"], "failed")
        self.assertEqual(self.store.user("alice")["identity"]["status"], "awaiting_reference")
        self.assertEqual(self.objects[self.paths["adapterPath"]], self.adapter)

    def test_split_training_still_requires_consent_before_upload_reads_or_gpu(self):
        self.store.db.data["users/alice"].pop("consentAt")
        job = self.train_job | {"status": "running", "leaseOwner": "worker", "leaseToken": "token",
                                "leaseExpiresAt": now() + timedelta(minutes=2)}
        self.store.db.data["jobs/train1"] = job
        with self.assertLogs(level="ERROR"):
            self.worker.run_job("train1", job)
        self.store.download.assert_not_called()
        self.comfy.train.assert_not_called()
        self.assertEqual(self.store.db.data["jobs/train1"]["status"], "failed")

    def test_partial_publish_can_retry_same_draft_then_new_draft_without_overwrite(self):
        self.install_cached()
        original_upload = self.store.upload.side_effect
        def lose_manifest(path, data, content_type):
            if path.endswith("/manifest.json"):
                raise RuntimeError("connection interrupted after reference upload")
            original_upload(path, data, content_type)
        self.store.upload.side_effect = lose_manifest
        with self.assertRaises(RuntimeError):
            self.finish()
        first_paths = identity_paths("alice", "train1", self.draft["uploadId"])
        self.assertIn(first_paths["referencePath"], self.objects)
        # Retry with the same draft is idempotent even if the local profile is restored.
        with self.assertRaises(RuntimeError):
            self.finish()
        new_upload = str(uuid.uuid4())
        new_draft = self.draft | {"uploadId": new_upload, "selfiePath": f"users/alice/uploads/{new_upload}/selfie.jpg"}
        self.store.db.data["users/alice/onboarding/current"] = new_draft
        self.objects[new_draft["selfiePath"]] = png(color="blue")
        self.store.upload.side_effect = original_upload
        self.finish()
        new_paths = identity_paths("alice", "train1", new_upload)
        self.assertEqual(self.objects[first_paths["referencePath"]], png(color="red"))
        self.assertEqual(self.objects[new_paths["referencePath"]], png(color="blue"))
        self.assertEqual(self.store.user("alice")["identity"]["referenceVersion"], new_upload)
        self.comfy.train.assert_not_called()

    def test_generation_restoration_accepts_new_reference_paths_and_actual_steps(self):
        self.finish()
        ready = self.store.user("alice")["identity"]
        result = self.worker.identity_profile("alice", ready, self.folder, Mock())
        self.assertEqual(result, ready["profileId"])
        self.comfy.train.assert_not_called()
        paths = identity_paths("alice", "train1", self.draft["uploadId"])
        manifest = json.loads(self.objects[paths["manifestPath"]])
        manifest["referenceVersion"] = str(uuid.uuid4())
        self.objects[paths["manifestPath"]] = json.dumps(manifest).encode()
        with self.assertRaises(JobError):
            self.worker.identity_profile("alice", ready, self.folder, Mock())

    def test_comfy_progress_and_submission_use_actual_steps(self):
        client = ComfyClient()
        client.json = Mock(side_effect=[{"job": {"job_id": "local1"}}, {"job": {"status": "running", "current_step": 40}}, {"job": {"status": "completed"}}])
        client.train("person", steps=80)
        self.assertEqual(client.json.call_args.kwargs["json"], {"steps": 80})
        progress = Mock()
        client.wait_training("local1", Mock(), progress, steps=80, interval=0)
        progress.assert_called_once_with(0.5)


if __name__ == "__main__":
    unittest.main()
