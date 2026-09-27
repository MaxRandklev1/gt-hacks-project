from contextlib import contextmanager
import copy
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image

from services.worker.comfy import ComfyClient, GenerationEvents, patch_personal_graph
from services.worker.personal import PersonalProgress, png_bytes
from services.worker.worker import JobError, Worker, now
from test_personal import figure
from test_worker import fake_store, Transaction


def enrollment(**changes):
    stamp, upload = now(), str(uuid.uuid4())
    return {"uid": "alice", "kind": "enroll", "requestVersion": 5, "status": "queued", "createdAt": stamp,
            "uploadId": upload, "selfiePath": f"users/alice/uploads/{upload}/selfie.jpg",
            "selfieSource": "upload", "selfieSelectedAt": stamp} | changes


class QueueProgressTests(unittest.TestCase):
    def test_only_unclaimed_enrollments_receive_counts_and_claim_clears_them(self):
        entries = {"jobs/enroll1": enrollment(), "jobs/running": enrollment(status="running"),
                   "jobs/completed": enrollment(status="completed"), "jobs/leased": enrollment(leaseToken="held"),
                   "jobs/generate": enrollment(kind="generate"), "jobs/bad": enrollment(uid="../foreign")}
        before = copy.deepcopy(entries)
        store = fake_store(entries)
        self.assertEqual(store.queued_enrollment_progress({"completed": 3, "total": 20}), 1)
        updated = store.db.data["jobs/enroll1"]
        self.assertEqual(updated["status"], "queued")
        self.assertEqual(updated["preparation"], {"completed": 3, "total": 20})
        self.assertIsNone(updated["progress"])
        self.assertIn("shared by everyone", updated["message"])
        self.assertNotIn("startedAt", updated)
        self.assertIsNotNone(updated["workerSeenAt"])
        for path in entries.keys() - {"jobs/enroll1"}:
            self.assertEqual(entries[path], before[path])
        claimed = store.claim("worker", {"enroll"})
        self.assertEqual(claimed[0], "enroll1")
        current = store.db.data["jobs/enroll1"]
        self.assertEqual(current["stage"], "starting")
        self.assertIsNone(current["preparation"])
        self.assertIsNone(current["sampling"])
        self.assertIsNotNone(current["startedAt"])

    def test_claim_between_query_and_transaction_cannot_be_overwritten(self):
        store = fake_store({"jobs/enroll1": enrollment()})
        def race(callback):
            store.db.data["jobs/enroll1"].update(status="running", leaseToken="new-owner", stage="creating_look")
            return callback(Transaction())
        store.transaction = race
        self.assertEqual(store.queued_enrollment_progress({"completed": 1, "total": 2}), 0)
        self.assertEqual(store.db.data["jobs/enroll1"]["stage"], "creating_look")
        self.assertNotIn("preparation", store.db.data["jobs/enroll1"])

    def test_incomplete_setup_never_claims_ready_and_success_returns_to_waiting(self):
        store = fake_store({"jobs/enroll1": enrollment()})
        store.queued_enrollment_progress({"completed": 19, "total": 20}, unavailable=True)
        job = store.db.data["jobs/enroll1"]
        self.assertEqual(job["stage"], "preparing_catalog")
        self.assertIn("could not be prepared", job["message"])
        self.assertEqual(job["status"], "queued")
        store.queued_enrollment_progress()
        self.assertEqual(job["stage"], "waiting_for_worker")
        self.assertIsNone(job["preparation"])
        for counts in ({"completed": 2, "total": 1}, {"completed": True, "total": 1}, {"completed": 0, "total": 251}):
            with self.assertRaises(ValueError):
                store.queued_enrollment_progress(counts)

    def test_prewarm_reports_only_successful_variants_and_does_not_claim_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            store, comfy = Mock(), Mock()
            worker = Worker(store, comfy, Mock(), {}, Path(temp), selfie_validator=Mock())
            store.active_garments.return_value = [(name, {"active": True, "imagePath": f"garments/{name}/reference.png",
                                                         "baseImagePath": f"garments/{name}/base.png"}) for name in ("good", "bad")]
            comfy.idle.return_value = True
            def render(name, garment, paths, folder, guard, notify):
                guard()  # Same callback invoked while waiting for accepted GPU work.
                if name == "bad":
                    raise JobError("Not prepared")
                return "render", (Path("a"), Path("b")), "pose"
            worker.styled_garment = Mock(side_effect=render)
            worker.prepare_pose = Mock(return_value=False)
            worker.garment_masks = Mock()
            with patch("services.worker.worker.time.monotonic", side_effect=itertools.count(0, 11)), self.assertLogs(level="ERROR"):
                result = worker.prewarm(warm_swapper=False, notify_queued=True)
            self.assertEqual(result["variantsPrepared"], 1)
            calls = store.queued_enrollment_progress.call_args_list
            self.assertEqual(calls[0].args[0], {"completed": 0, "total": 2})
            self.assertTrue(all(call.args[0]["completed"] <= 1 for call in calls))
            self.assertEqual(calls[-1].kwargs, {"unavailable": True})
            store.claim.assert_not_called()
            comfy.submit.assert_not_called()


class GenerationObservationTests(unittest.TestCase):
    def test_only_bound_prompt_actual_sampler_steps_are_accepted(self):
        message = {"type": "progress", "data": {"prompt_id": "mine", "node": "9", "value": 4, "max": 24}}
        self.assertEqual(GenerationEvents.event(message, "mine"), {"node": "9", "step": 4, "total": 24})
        for changes in ({"prompt_id": "other"}, {"prompt_id": None}, {"node": "22"}, {"value": True},
                        {"value": -1}, {"value": 25}, {"max": 0}, {"max": 10001}, {"value": 4.0}):
            with self.subTest(changes=changes):
                self.assertIsNone(GenerationEvents.event(message | {"data": message["data"] | changes}, "mine"))
        self.assertIsNone(GenerationEvents.event({"type": "executing", "data": {"node": "9", "prompt_id": None}}, "mine"))

    def test_binary_previews_are_ignored_and_reads_are_bounded(self):
        events = GenerationEvents("http://127.0.0.1:8188", "job")
        events.socket = Mock()
        message = json.dumps({"type": "progress", "data": {"prompt_id": "mine", "node": "9", "value": 1, "max": 24}})
        events.socket.recv_data.side_effect = [(2, b"private-image-preview"), (1, "malformed"), (1, message), TimeoutError()]
        with patch("services.worker.comfy.time.monotonic", return_value=0):
            self.assertEqual(events.read("mine"), [{"node": "9", "step": 1, "total": 24}])
        events.socket.recv_data.side_effect = None
        events.socket.recv_data.return_value = (2, b"preview")
        events.socket.recv_data.reset_mock()
        with patch("services.worker.comfy.time.monotonic", return_value=0):
            self.assertEqual(events.read("mine"), [])
        self.assertEqual(events.socket.recv_data.call_count, 64)

    def test_socket_failure_preserves_history_completion_and_old_callback_contract(self):
        with patch("websocket.create_connection", side_effect=OSError("offline")), self.assertLogs(level="WARNING"):
            with GenerationEvents("http://127.0.0.1:8188", "job") as events:
                self.assertEqual(events.read("mine"), [])
                client = ComfyClient()
                history = {"status": {"status_str": "success"}}
                client.json = Mock(side_effect=[{}, {"mine": history}])
                callback, guard = Mock(), Mock()
                with patch("services.worker.comfy.time.sleep"):
                    self.assertEqual(client.wait_generation("mine", guard, callback, events=events, on_event=Mock()), history)
                callback.assert_called_once_with()
                self.assertEqual(guard.call_count, 2)
                self.assertTrue(all(c.args[0] == "GET" for c in client.json.call_args_list))

    def test_disconnect_drops_sampling_precision_without_resubmission(self):
        events = GenerationEvents("http://127.0.0.1:8188", "job")
        events.socket = Mock()
        socket = events.socket
        socket.recv_data.side_effect = OSError("disconnected")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(events.read("mine"), [{"unavailable": True}])
        self.assertIsNone(events.socket)
        socket.close.assert_called_once_with(timeout=1)


class PersonalProgressTests(unittest.TestCase):
    def test_stage_counts_are_observed_throttled_and_heartbeat_is_not_fake_progress(self):
        details = Mock()
        progress = PersonalProgress(Mock(), details)
        graph = {"34": {"class_type": "ImageUpscaleWithModel"}, "35": {"class_type": "SaveImage"}}
        progress.observe_graph(graph)
        clock = [100.0]
        with patch("services.worker.personal.time.monotonic", side_effect=lambda: clock[0]):
            progress.stage("creating_look", "Starting")
            progress.event({"node": "22", "step": 6, "total": 40})
            self.assertIsNone(details.call_args.args[0]["sampling"])
            progress.event({"node": "9", "step": 5, "total": 24})
            self.assertEqual(details.call_count, 1)
            clock[0] += 1.1
            progress.heartbeat()
            self.assertEqual(details.call_args.args[0]["sampling"], {"step": 5, "total": 24})
            self.assertIsNone(details.call_args.args[0]["progress"])
            progress.event({"node": "9", "step": 3, "total": 24})
            progress.event({"node": "9", "step": 7, "total": 80})
            progress.event({"node": "9"})
            self.assertEqual(progress.fields["sampling"], {"step": 5, "total": 24})
            clock[0] += 11
            progress.heartbeat()
            self.assertEqual(details.call_args.args[0]["sampling"], {"step": 5, "total": 24})
            progress.event({"unavailable": True})
            self.assertIsNone(details.call_args.args[0]["sampling"])
            progress.event({"node": "34"})
            self.assertEqual(details.call_args.args[0]["stage"], "upscaling_look")
            progress.event({"node": "9", "step": 24, "total": 24})
            progress.event({"node": "9"})
            self.assertEqual(progress.fields["stage"], "upscaling_look")
            self.assertIsNone(progress.fields["sampling"])
        progress.observe_graph({"34": {"class_type": "KSampler"}})
        progress.stage("creating_look", "Starting")
        progress.event({"node": "34"})
        self.assertEqual(details.call_args.args[0]["stage"], "creating_look")

    def test_personal_graph_is_unchanged_and_observer_connects_before_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            selfie = folder / "selfie.png"
            Image.new("RGB", (200, 200)).save(selfie)
            store, comfy, details = Mock(), Mock(), Mock()
            worker = Worker(store, comfy, Mock(), {}, folder / "state", selfie_validator=Mock())
            worker.human = Mock(return_value=Mock(parse=Mock(return_value=figure())))
            worker.pose_ready = Mock(return_value=True)
            worker.pose = Mock(return_value={"b1024": Image.new("RGB", (200, 200)), "b2k": Image.new("RGB", (400, 400)), "labels": figure()})
            actions = []
            @contextmanager
            def observe(job):
                actions.append("connected")
                yield Mock()
                actions.append("closed")
            comfy.progress_events.side_effect = observe
            comfy.create_profile.return_value = {"id": "profile"}
            comfy.upload_image.return_value = "base.png"
            comfy.submit.side_effect = lambda *args: actions.append("submitted") or "prompt"
            comfy.output.side_effect = lambda history, node: png_bytes(Image.new("RGB", (400, 400) if node == "35" else (200, 200)))
            worker.personal_base("alice", "enroll1", selfie, "pose", Mock(), Mock(), details=details)
            self.assertEqual(actions, ["connected", "submitted", "closed"])
            expected = patch_personal_graph(worker.personal_template, base_image="base.png", profile_id="profile", job_id="enroll1", steps=24, base_outputs=False)
            self.assertEqual(comfy.submit.call_args.args[0], expected)
            self.assertEqual(comfy.submit.call_count, 1)
            self.assertEqual(details.call_args.args[0]["stage"], "fitting_look")
            self.assertTrue(all(call.args[0]["progress"] is None for call in details.call_args_list))
            store.upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
