"""CPU-only tests; never launch the trainer, import torch, or access user photos."""
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from backend import ProfileError, ProfileStore, TrainingManager, data_warnings, local_request_allowed, selected_photos, within, write_json
from training_worker import build_arguments, dataset_cache_path


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.training = self.root / "training"
        self.training.mkdir()
        self.store = ProfileStore(config={"training_root": str(self.training), "profiles_root": str(self.training / "profiles")})
        self.image = self.root / "source.png"
        Image.new("RGB", (320, 480), (100, 120, 140)).save(self.image)

    def tearDown(self):
        self.temp.cleanup()

    def make_profile(self, name="Sample person", count=1):
        return self.store.create(name, [(f"picture-{i}.png", self.image) for i in range(count)])

    def test_profiles_unique_and_captions_neutral(self):
        first = self.make_profile("../../Someone")
        second = self.make_profile("../../Someone")
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["trigger"], second["trigger"])
        self.assertEqual(first["photos"][0]["caption"], f"photo of {first['trigger']}, a person")
        self.assertTrue(self.store.photo_path(first, "p0000").is_relative_to(self.store.root))
        self.assertFalse(self.store.public(first)["training"]["adapter_available"])
        self.assertTrue(data_warnings(first))

    def test_selection_preserves_order_and_keeps_one_image(self):
        profile = self.make_profile(count=3)
        updated = self.store.update(profile["id"], {"selected_photo_ids": ["p0002", "p0000"]})
        self.assertEqual([p["id"] for p in selected_photos(updated)], ["p0000", "p0002"])
        updated = self.store.update(profile["id"], {"selected_photo_ids": ["p0001"]})
        self.assertEqual(len(selected_photos(updated)), 1)
        with self.assertRaises(ProfileError):
            self.store.update(profile["id"], {"selected_photo_ids": []})

    def test_small_image_is_warned_not_removed(self):
        Image.new("RGB", (64, 80)).save(self.image)
        profile = self.make_profile()
        self.assertTrue(profile["photos"][0]["selected"])
        self.assertTrue(any("small" in warning for warning in data_warnings(profile)))

    def test_live_selfie_reference_is_separate_and_never_falls_back(self):
        profile = self.make_profile(count=5)
        original_photos = json.loads(json.dumps(profile["photos"]))
        profile["cloud_identity"] = {"uid": "alice", "version": "run1"}
        profile["inference_reference_required"] = True
        with self.assertRaises(ProfileError):
            self.store.reference_path(profile, 0)
        path = self.store.profile_dir(profile["id"]) / "cloud-reference/reference.png"
        path.parent.mkdir()
        Image.new("RGB", (320, 480), "red").save(path)
        profile["inference_reference"] = {"source": "live_selfie", "filename": "cloud-reference/reference.png",
            "owner": profile["cloud_identity"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.assertEqual(self.store.reference_path(profile, 0), path)
        self.assertEqual(self.store.reference_path(profile, 4), path)
        self.assertEqual(profile["photos"], original_photos)
        self.assertEqual(len(selected_photos(profile)), 5)
        self.assertEqual(self.store.public(profile)["reference_source"], "live_selfie")
        profile["inference_reference"]["source"] = "recent_selfie"
        self.assertEqual(self.store.reference_path(profile, 4), path)
        self.assertEqual(self.store.public(profile)["reference_source"], "recent_selfie")
        profile["inference_reference"]["source"] = "unknown"
        with self.assertRaises(ProfileError):
            self.store.reference_path(profile, 0)
        profile["inference_reference"]["source"] = "recent_selfie"
        profile["inference_reference"]["owner"] = {"uid": "bob", "version": "run1"}
        with self.assertRaises(ProfileError):
            self.store.reference_path(profile, 0)
        profile["inference_reference"]["owner"] = profile["cloud_identity"]
        path.write_bytes(b"changed")
        with self.assertRaises(ProfileError):
            self.store.reference_path(profile, 0)

    def test_legacy_reference_selection_still_works(self):
        profile = self.make_profile(count=2)
        self.assertEqual(self.store.reference_path(profile, 1), self.store.photo_path(profile, "p0001"))

    def test_identity_node_emits_selfie_pixels_with_no_selected_training_photos(self):
        import numpy as np
        from unittest.mock import Mock
        profile = self.make_profile()
        profile["photos"][0]["selected"] = False
        profile["cloud_identity"] = {"uid": "alice", "version": "run1"}
        profile["inference_reference_required"] = True
        path = self.store.profile_dir(profile["id"]) / "cloud-reference/reference.png"
        path.parent.mkdir()
        Image.new("RGB", (320, 480), "red").save(path)
        profile["inference_reference"] = {"source": "recent_selfie", "filename": "cloud-reference/reference.png",
            "owner": profile["cloud_identity"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.store.save(profile)
        name = "identity_node_selfie_test"
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("__init__.py"),
                                                      submodule_search_locations=[str(Path(__file__).parent)])
        module = importlib.util.module_from_spec(spec)
        # Stub only the tensor boundary: this test must not import torch/CUDA or ComfyUI.
        tensor = lambda array: SimpleNamespace(unsqueeze=lambda axis: np.expand_dims(array, axis))
        with patch.dict(sys.modules, {name: module, "server": SimpleNamespace(PromptServer=SimpleNamespace(instance=None)),
                                      "torch": SimpleNamespace(from_numpy=tensor)}):
            spec.loader.exec_module(module)
            module.STORE = self.store
            module.MANAGER = Mock()
            module.MANAGER.active_job.return_value = None
            result = module.UniversalIdentityProfile().apply_identity(object(), profile["id"], 0, 0, False)
            image = result["result"][1]
            self.assertEqual(image.shape, (1, 480, 320, 3))
            self.assertTrue(np.all(image[..., 0] == 1.0))
            self.assertTrue(np.all(image[..., 1:] == 0.0))

    def test_traversal_and_adapter_escape_rejected(self):
        with self.assertRaises(ProfileError):
            self.store.load("../escape")
        with self.assertRaises(ProfileError):
            within(self.store.root, "../escape")
        profile = self.make_profile()
        profile["latest_successful"] = {"adapter_relative": "../../secret.safetensors"}
        with self.assertRaises(ProfileError):
            self.store.adapter_path(profile)

    def test_failed_upload_cleans_only_its_new_directory(self):
        good = self.make_profile()
        broken = self.root / "broken.png"
        broken.write_bytes(b"not an image")
        with self.assertRaises(OSError):
            self.store.create("broken", [("bad.png", broken)])
        self.assertEqual([p["id"] for p in self.store.list_profiles()], [good["id"]])

    def test_reservation_serializes_jobs_and_preserves_prior_adapter(self):
        profile = self.make_profile()
        for relative in (".venv/Scripts/python.exe", "trainer/train_identity_qwen21.py", "trainer/nf4.json", "models/Qwen-Image-2.1/model_index.json"):
            path = self.training / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test placeholder", encoding="utf-8")
        adapter = self.store.profile_dir(profile["id"]) / "adapters" / "existing.safetensors"
        adapter.parent.mkdir()
        adapter.write_bytes(b"test placeholder")
        profile["latest_successful"] = {"adapter_relative": "adapters/existing.safetensors", "steps": 400}
        self.store.save(profile)
        manager = TrainingManager(self.store)
        job = manager.reserve(profile["id"], 2)
        self.assertTrue(manager.status()["active"])
        with self.assertRaises(ProfileError):
            manager.reserve(profile["id"], 400)
        manager.fail_reserved(job, "Synthetic failure")
        self.assertFalse(manager.status()["active"])
        self.assertEqual(self.store.adapter_path(self.store.load(profile["id"])), adapter)

    def test_completed_process_still_reserves_gpu_until_exit(self):
        profile = self.make_profile()
        run_dir = self.store.profile_dir(profile["id"]) / "runs" / "run-test"
        run_dir.mkdir(parents=True)
        job_path = run_dir / "job.json"
        job = {"job_id": "run-test", "profile_id": profile["id"], "status": "completed", "pid": 123456,
               "launcher_pid": 123457, "created_at": 0, "max_steps": 2, "output_relative": "runs/run-test/output"}
        write_json(job_path, job)
        manager = TrainingManager(self.store)
        write_json(manager.lock_path, {"job_relative": str(job_path.relative_to(self.store.root))})
        with patch("backend.process_alive", side_effect=lambda pid: pid == 123456):
            active = manager.active_job()
            self.assertTrue(active["finalizing"])
            self.assertTrue(manager.lock_path.exists())
        with patch("backend.process_alive", return_value=False):
            self.assertIsNone(manager.active_job())
            self.assertFalse(manager.lock_path.exists())

    def test_worker_arguments_person_neutral_and_local(self):
        args = build_arguments(self.training, self.root / "dataset", self.root / "output", "u123_person", 400)
        self.assertEqual(args[args.index("--instance_prompt") + 1], "a photo of u123_person, a person")
        self.assertIn("--text_encoder_4bit", args)
        self.assertEqual(args[args.index("--rank") + 1], args[args.index("--lora_alpha") + 1])
        self.assertEqual(args[args.index("--lr_warmup_steps") + 1], "0")
        self.assertIn("512,512", args[args.index("--aspect_ratio_buckets") + 1])
        self.assertIn("384,512", args[args.index("--aspect_ratio_buckets") + 1])
        self.assertEqual(args[args.index("--max_train_steps") + 1], "400")
        cache = Path(args[args.index("--cache_dir") + 1])
        self.assertTrue(cache.is_relative_to(self.training))
        self.assertNotEqual(cache, dataset_cache_path(self.training, self.root / "another-output"))

    def test_real_cpu_worker_registers_interpreter_and_retains_diagnostic_adapter(self):
        # This trainer stub uses only stdlib. It verifies the real Windows
        # launcher/worker handshake without importing ML libraries or any GPU.
        profile = self.make_profile()
        for relative in (".venv/Scripts/python.exe", "trainer/nf4.json", "models/Qwen-Image-2.1/model_index.json"):
            path = self.training / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test placeholder", encoding="utf-8")
        fake_trainer = self.training / "trainer/train_identity_qwen21.py"
        fake_trainer.write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "assert os.environ.get('HF_HUB_OFFLINE') == '1'\n"
            "target=Path(sys.argv[sys.argv.index('--output_dir')+1]); target.mkdir()\n"
            "(target/'pytorch_lora_weights.safetensors').write_bytes(b'CPU TEST STUB')\n"
            "(target/'training_health.json').write_text(json.dumps({'status':'completed','completed_updates':2}))\n",
            encoding="utf-8")
        config_path = self.root / "config.json"
        write_json(config_path, {"training_root": str(self.training), "profiles_root": str(self.store.root)})
        self.store.config_path = config_path
        manager = TrainingManager(self.store)
        job = manager.reserve(profile["id"], 2)
        job_path = manager.job_path(job["job_id"])
        environment = os.environ.copy()
        environment["HF_HUB_OFFLINE"] = "1"
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("training_worker.py")),
            "--config", str(config_path), "--job", str(job_path), "--launch-token", job["launch_token"]],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job.update(status="running", pid=process.pid, launcher_pid=process.pid)
        write_json(job_path, job)
        output, _ = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, output.decode("utf-8", errors="replace"))
        recorded = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(recorded["status"], "completed")
        self.assertGreater(recorded["pid"], 0)
        self.assertFalse(manager.status()["active"])
        self.assertIsNone(self.store.load(profile["id"])["latest_successful"])


class OriginTests(unittest.TestCase):
    def request(self, **kwargs):
        values = {"remote": "127.0.0.1", "host": "127.0.0.1:8000", "headers": {"Origin": "http://127.0.0.1:8000", "X-Universal-Identity": "1"}}
        values.update(kwargs)
        return SimpleNamespace(**values)

    def test_same_local_origin_allowed(self):
        self.assertTrue(local_request_allowed(self.request(), True))
        self.assertTrue(local_request_allowed(self.request(remote="::ffff:127.0.0.1"), True))

    def test_remote_or_cross_origin_denied(self):
        self.assertFalse(local_request_allowed(self.request(remote="192.168.0.20")))
        self.assertFalse(local_request_allowed(self.request(host="evil.example:8000")))
        self.assertFalse(local_request_allowed(self.request(headers={"Origin": "https://evil.example", "X-Universal-Identity": "1"}), True))
        self.assertFalse(local_request_allowed(self.request(headers={"Origin": "http://127.0.0.1:9000", "X-Universal-Identity": "1"}), True))
        self.assertFalse(local_request_allowed(self.request(headers={"Origin": "null", "X-Universal-Identity": "1"}), True))

    def test_mutations_require_header(self):
        self.assertTrue(local_request_allowed(self.request(headers={})))
        self.assertFalse(local_request_allowed(self.request(headers={}), True))


if __name__ == "__main__":
    unittest.main()
