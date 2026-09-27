import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from services.worker.body_templates import BODY_TEMPLATE_IDS, select_body_template
from services.worker.personal import CompositeError, personal_paths, png_bytes
from services.worker.worker import JobError, Worker, main, now, saved_body_template, sha256, validate_garment


def catalog(garment_id="shirt"):
    prefix = f"garments/{garment_id}"
    return {"active": True, "name": "Tee", "imagePath": prefix + "/reference.png",
            "baseImagePath": prefix + "/base.png", "updatedAt": now(),
            "bodyBaseImagePaths": {key: prefix + ("/base.png" if key == "weight-3" else f"/body-bases/{key}.png")
                                  for key in BODY_TEMPLATE_IDS}}


def png(size=16, color="navy"):
    return png_bytes(Image.new("RGB", (size, size), color))


def make(temp):
    store, comfy = Mock(), Mock()
    comfy.idle.return_value = True
    worker = Worker(store, comfy, Mock(), {}, Path(temp) / "state", selfie_validator=Mock())
    return worker, store, comfy


class BodyPrewarmTests(unittest.TestCase):
    def test_all_variants_prepare_only_five_poses_without_personal_work(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy = make(temp)
            store.active_garments.return_value = [(name, catalog(name)) for name in ("shirt", "hoodie")]
            worker.styled_garment = Mock(side_effect=lambda gid, garment, paths, *args: (
                gid + paths["baseImagePath"].split("/")[-1], (Path("a.png"), Path("b.png")),
                paths["baseImagePath"].split("/")[-1]))
            prepared = set()
            def prepare(base, guard):
                fresh = base not in prepared
                prepared.add(base)
                return fresh
            worker.prepare_pose = Mock(side_effect=prepare)
            worker.garment_masks, worker.personal_base = Mock(), Mock()
            report = worker.prewarm(warm_swapper=False, strict=True)
            self.assertEqual(report, {"variantsPrepared": 10, "posesPrepared": 5, "failures": []})
            self.assertEqual(worker.styled_garment.call_count, 10)
            self.assertEqual(worker.garment_masks.call_count, 10)
            for call in worker.styled_garment.call_args_list:
                self.assertIn(call.args[2]["baseImagePath"], call.args[1]["bodyBaseImagePaths"].values())
            worker.personal_base.assert_not_called()
            comfy.create_profile.assert_not_called()
            comfy.submit.assert_not_called()
            store.claim.assert_not_called()

    def test_filtered_prewarm_and_busy_gpu_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy = make(temp)
            store.active_garments.return_value = [(name, catalog(name)) for name in ("shirt", "hoodie")]
            worker.styled_garment = Mock(return_value=("render", (Path("a"), Path("b")), "pose"))
            worker.prepare_pose, worker.garment_masks = Mock(return_value=False), Mock()
            result = worker.prewarm(["weight-1", "weight-5"], ["hoodie"], warm_swapper=False, strict=True)
            self.assertEqual(result["variantsPrepared"], 2)
            self.assertEqual([c.args[0] for c in worker.styled_garment.call_args_list], ["hoodie", "hoodie"])
            self.assertEqual([c.args[2]["baseImagePath"] for c in worker.styled_garment.call_args_list],
                             ["garments/hoodie/body-bases/weight-1.png", "garments/hoodie/body-bases/weight-5.png"])
            worker.styled_garment.reset_mock()
            comfy.idle.return_value = False
            with self.assertLogs(level="ERROR"), self.assertRaisesRegex(JobError, "preparation failed"):
                worker.prewarm(["weight-1"], ["shirt"], warm_swapper=False, strict=True)
            worker.styled_garment.assert_not_called()
            comfy.submit.assert_not_called()

    def test_pose_graph_has_only_reachable_fixed_resize_and_upscale_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, _, comfy = make(temp)
            base_key = worker.register_pose_source(png())
            comfy.upload_image.return_value = "uploaded.png"
            comfy.output.side_effect = [png(16), png(32)]
            self.assertTrue(worker.prepare_pose(base_key, Mock()))
            graph = comfy.submit.call_args.args[0]
            self.assertEqual(set(graph), {"1", "31", "33", "39", "40", "41", "42", "43"})
            for node in graph.values():
                for value in node["inputs"].values():
                    if isinstance(value, list):
                        self.assertIn(value[0], graph)
            for node_id in ("31", "33", "39", "40", "41"):
                self.assertEqual(graph[node_id], worker.personal_template[node_id])
            self.assertTrue(worker.pose_ready(base_key))
            self.assertFalse(worker.prepare_pose(base_key, Mock()))
            comfy.submit.assert_called_once()
            comfy.create_profile.assert_not_called()

    def test_prewarm_cli_takes_worker_lock_and_never_claims_jobs(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            store, comfy, worker = Mock(), Mock(), Mock()
            comfy.idle.return_value = True
            worker.prewarm.return_value = {"variantsPrepared": 1, "posesPrepared": 1, "failures": []}
            state = Path(temp) / "state"
            with patch("services.worker.worker.FirebaseStore", return_value=store), \
                    patch("services.worker.worker.ComfyClient", return_value=comfy), \
                    patch("services.worker.worker.Worker", return_value=worker), \
                    patch("services.worker.worker.process_lock") as lock:
                main(["--project", "test", "--bucket", "test", "--profiles-root", temp,
                      "--state-dir", str(state), "--prewarm-only", "--body-template", "weight-2", "--garment", "shirt"])
                lock.assert_called_once_with(state / "gpu.lock")
                lock.return_value.__enter__.assert_called_once()
            worker.prewarm.assert_called_once_with(["weight-2"], ["shirt"], warm_swapper=False, strict=True)
            store.claim.assert_not_called()
            store.expire_abandoned.assert_not_called()
            worker.run_job.assert_not_called()


class BodyCacheTests(unittest.TestCase):
    def test_weight_three_reuses_exact_legacy_cache_and_other_body_cannot(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy = make(temp)
            garment = catalog()
            legacy = validate_garment("shirt", garment)
            selected = validate_garment("shirt", garment, "weight-3")
            self.assertEqual(selected, legacy)
            key = sha256(json.dumps(["shirt", legacy, garment["updatedAt"].isoformat(), worker.styled_template], sort_keys=True).encode())[:32]
            cache = worker.state_dir / "styled" / key
            cache.mkdir(parents=True)
            (cache / "styled-1024.png").write_bytes(png())
            (cache / "styled-2k.png").write_bytes(png(32))
            (cache / "base-key.txt").write_text("legacy-pose")
            result = worker.styled_garment("shirt", garment, selected, Path(temp), Mock(), Mock(), allow_render=False)
            self.assertEqual((result[0], result[2]), (key, "legacy-pose"))
            with self.assertRaisesRegex(JobError, "still being prepared"):
                worker.styled_garment("shirt", garment, validate_garment("shirt", garment, "weight-1"), Path(temp), Mock(), Mock(), allow_render=False)
            store.download.assert_not_called()
            comfy.submit.assert_not_called()


class BodyEnrollmentTests(unittest.TestCase):
    def setup_enroll(self, temp):
        worker, store, comfy = make(temp)
        garments = [(name, catalog(name)) for name in ("shirt", "hoodie", "tee", "jacket")]
        store.active_garments.return_value = garments
        worker.reference = Mock(return_value=(png(), {"selfieSource": "upload", "selfieSelectedAt": now()}))
        worker.styled_garment = Mock(return_value=("render", (Path("a.png"), Path("b.png")), "pose-weight-2"))
        worker.personal_base = Mock(return_value=(png(), png(32), png(color="black"), png(color="black")))
        worker.garment_masks = Mock(return_value={})
        worker.composite_tryon = Mock(side_effect=lambda personal, *args: (personal["p1024"], personal["p2k"]))
        user = {"heightCm": 180, "weightKg": 68, "measurementSystem": "us"}
        return worker, store, comfy, user, garments

    def test_enroll_then_scan_uses_frozen_body_after_measurements_change(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy, user, garments = self.setup_enroll(temp)
            lease = Mock()
            worker.enroll("enroll1", {}, "alice", user, Path(temp), lease)
            worker.personal_base.assert_called_once()
            self.assertEqual(worker.styled_garment.call_count, 4)
            for call in worker.styled_garment.call_args_list:
                self.assertTrue(call.args[2]["baseImagePath"].endswith("/weight-2.png"))
                self.assertFalse(call.kwargs["allow_render"])
            identity = next(call.kwargs["user_fields"]["identity"] for call in lease.write.call_args_list if "user_fields" in call.kwargs)
            expected = select_body_template(180, 68)
            self.assertEqual(identity["bodyTemplate"], expected)
            self.assertEqual(list(identity["personalBases"]), ["pose-weight-2"])
            manifest = json.loads(next(call.args[1] for call in store.upload.call_args_list if call.args[0].endswith("manifest.json")))
            self.assertEqual(manifest["bodyTemplate"], expected)
            # Exercise the real personal-assets ownership/hash validation on the files just saved by enrollment.
            worker.styled_garment.reset_mock()
            store.garment.return_value = garments[0][1]
            changed_user = user | {"weightKg": 120, "identity": identity}
            self.assertEqual(select_body_template(180, 120)["id"], "weight-5")
            scan_lease = Mock()
            worker.generate("scan1", {"garmentId": "shirt"}, "alice", changed_user, Path(temp), scan_lease)
            call = worker.styled_garment.call_args
            self.assertEqual(call.args[2]["baseImagePath"], "garments/shirt/body-bases/weight-2.png")
            self.assertFalse(call.kwargs["allow_render"])
            self.assertEqual(scan_lease.write.call_args.kwargs["generation"]["bodyTemplateId"], "weight-2")
            self.assertEqual(worker.composite_tryon.call_args.args[0]["p1024"].getpixel((0, 0)), (0, 0, 128))
            store.download.assert_not_called()
            comfy.submit.assert_not_called()
            comfy.train.assert_not_called()

    def test_different_pose_hashes_fail_before_personal_render(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, _, user, _ = self.setup_enroll(temp)
            worker.styled_garment.side_effect = lambda gid, *args, **kwargs: ("render", (Path("a"), Path("b")), gid)
            with self.assertRaisesRegex(JobError, "same pose"):
                worker.enroll("enroll1", {}, "alice", user, Path(temp), Mock())
            worker.personal_base.assert_not_called()
            store.upload.assert_not_called()

    def test_missing_variant_fails_before_personal_gpu_work(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy, user, _ = self.setup_enroll(temp)
            del worker.styled_garment  # Real cache lookup; no prepared files exist.
            with self.assertRaisesRegex(JobError, "still being prepared"):
                worker.enroll("enroll1", {}, "alice", user, Path(temp), Mock())
            worker.personal_base.assert_not_called()
            store.download.assert_not_called()
            store.upload.assert_not_called()
            comfy.submit.assert_not_called()

    def test_wrong_pose_never_reaches_compositor(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, _, user, garments = self.setup_enroll(temp)
            lease = Mock()
            worker.enroll("enroll1", {}, "alice", user, Path(temp), lease)
            identity = next(call.kwargs["user_fields"]["identity"] for call in lease.write.call_args_list if "user_fields" in call.kwargs)
            store.garment.return_value = garments[0][1]
            worker.styled_garment.return_value = ("wrong-render", (Path("a"), Path("b")), "pose-weight-5")
            with self.assertRaisesRegex(JobError, "different pose"):
                worker.generate("scan1", {"garmentId": "shirt"}, "alice", user | {"identity": identity}, Path(temp), Mock())
            worker.composite_tryon.assert_not_called()

    def test_legacy_identity_keeps_original_body_despite_current_measurements(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, _, user, garments = self.setup_enroll(temp)
            store.garment.return_value = garments[0][1]
            worker.personal_assets = Mock(return_value={"p1024": Image.new("RGB", (16, 16)), "p2k": Image.new("RGB", (32, 32))})
            legacy = {"status": "ready", "mode": "personal_base", "version": "old"}
            worker.generate("scan1", {"garmentId": "shirt"}, "alice", user | {"weightKg": 120, "identity": legacy}, Path(temp), Mock())
            self.assertEqual(worker.styled_garment.call_args.args[2]["baseImagePath"], "garments/shirt/base.png")
            self.assertTrue(worker.styled_garment.call_args.kwargs["allow_render"])

    def test_previous_policy_identity_still_scans_its_saved_body_after_update(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy, user, garments = self.setup_enroll(temp)
            height, weight = 68 * 2.54, 170 * 0.45359237
            snapshot = select_body_template(height, weight, policy_version="bmi-visual-v1")
            self.assertEqual(snapshot["id"], "weight-4")
            self.assertEqual(select_body_template(height, weight)["id"], "weight-3")
            assets = {"p1024": png(), "p2k": png(32), "hair": png(color="black"), "tee": png(color="black")}
            paths = personal_paths("alice", "old-enroll", "pose-weight-4")
            record = paths | {name + "Sha256": sha256(data) for name, data in assets.items()}
            identity = {"status": "ready", "mode": "personal_base", "version": "old-enroll",
                        "bodyTemplate": snapshot, "personalBases": {"pose-weight-4": record}}
            worker.cache_personal("alice", "old-enroll", "pose-weight-4", assets)
            store.garment.return_value = garments[0][1]
            worker.styled_garment.return_value = ("old-render", (Path("a"), Path("b")), "pose-weight-4")
            lease = Mock()
            # Current profile measurements also differ: neither the new policy nor new measurements reroutes the old look.
            worker.generate("scan1", {"garmentId": "shirt"}, "alice", user | {"weightKg": 120, "identity": identity}, Path(temp), lease)
            self.assertEqual(worker.styled_garment.call_args.args[2]["baseImagePath"], "garments/shirt/body-bases/weight-4.png")
            self.assertFalse(worker.styled_garment.call_args.kwargs["allow_render"])
            self.assertEqual(lease.write.call_args.kwargs["generation"]["bodyTemplateId"], "weight-4")
            self.assertEqual(worker.composite_tryon.call_args.args[0]["p1024"].getpixel((0, 0)), (0, 0, 128))
            worker.personal_base.assert_not_called()
            store.download.assert_not_called()
            comfy.submit.assert_not_called()

    def test_invalid_saved_snapshot_is_not_silently_reselected(self):
        selected = select_body_template(180, 68)
        self.assertEqual(saved_body_template({"bodyTemplate": selected}), "weight-2")
        self.assertIsNone(saved_body_template({}))
        previous = select_body_template(68 * 2.54, 170 * 0.45359237, policy_version="bmi-visual-v1")
        self.assertEqual(saved_body_template({"bodyTemplate": previous}), "weight-4")
        for bad in (None, {}, selected | {"id": "weight-5"}, selected | {"weightKg": True}, selected | {"bmi": 0},
                    selected | {"policyVersion": "bmi-visual-v3"}, selected | {"policyVersion": None},
                    {key: value for key, value in selected.items() if key != "policyVersion"}, previous | {"id": "weight-3"}):
            with self.subTest(bad=bad), self.assertRaises(JobError):
                saved_body_template({"bodyTemplate": bad})


if __name__ == "__main__":
    unittest.main()
