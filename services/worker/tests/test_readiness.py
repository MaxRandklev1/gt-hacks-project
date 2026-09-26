import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from services.worker.worker import JobError, check_readiness, main


TEMPLATE = Path(__file__).resolve().parents[3] / "comfy-identity/Qwen21_Universal_TryOn_4K.api.json"
STYLED = Path(__file__).resolve().parents[3] / "comfy-identity/Qwen21_Garment_Styled_2K.api.json"
SWAP = Path(__file__).resolve().parents[3] / "comfy-identity/FaceSwap_TryOn_2K.api.json"


def inputs():
    graph = json.loads(TEMPLATE.read_text())
    schemas = {}
    model_keys = {"unet_name", "clip_name", "vae_name", "lora_name", "bg_removal_name", "model_name"}
    for node in [node for path in (TEMPLATE, STYLED, SWAP) for node in json.loads(path.read_text()).values()]:
        required = schemas.setdefault(node["class_type"], {"input": {"required": {}}})["input"]["required"]
        for key, value in node["inputs"].items():
            if key in model_keys:
                required.setdefault(key, ["COMBO", {"options": []}])[1]["options"].append(value)
    comfy = Mock()
    comfy.json.return_value = schemas
    comfy.idle.return_value = True
    store = Mock()
    store.db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []
    store.bucket.list_blobs.return_value = []
    return graph, schemas, comfy, store


class ReadinessTests(unittest.TestCase):
    def test_checks_are_read_only_and_do_not_expose_profile_details(self):
        graph, _, comfy, store = inputs()
        with tempfile.TemporaryDirectory() as temp:
            result = check_readiness(store, comfy, temp, graph)
        self.assertTrue(result["ready"])
        self.assertFalse(result["checks"]["activeCatalogEntryFound"])
        comfy.json.assert_called_once_with("GET", "/object_info")
        comfy.submit.assert_not_called()
        comfy.create_profile.assert_not_called()
        comfy.train.assert_not_called()
        store.claim.assert_not_called()
        store.update.assert_not_called()
        store.bucket.list_blobs.assert_called_once_with(prefix="garments/", max_results=1)
        store.bucket.reload.assert_not_called()
        self.assertNotIn(temp, json.dumps(result))

    def test_fast_path_graphs_are_checked_for_missing_nodes(self):
        graph, schemas, comfy, store = inputs()
        del schemas["AdvancedSwapFaceImage"]
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(check_readiness(store, comfy, temp, graph)["ready"])  # Legacy-only check.
            with self.assertRaises(JobError):
                check_readiness(store, comfy, temp, graph, None, json.loads(STYLED.read_text()), json.loads(SWAP.read_text()))
        comfy.submit.assert_not_called()

    def test_missing_models_fail_before_firebase_or_gpu_work(self):
        graph, schemas, comfy, store = inputs()
        schemas["UpscaleModelLoader"]["input"]["required"]["model_name"][1]["options"] = []
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(JobError):
            check_readiness(store, comfy, temp, graph)
        store.bucket.list_blobs.assert_not_called()
        comfy.submit.assert_not_called()

    def test_config_must_match_profiles_and_trainer_prerequisites(self):
        graph, _, comfy, store = inputs()
        with tempfile.TemporaryDirectory() as temp:
            training = Path(temp)
            profiles = training / "profiles"
            profiles.mkdir()
            config = training / "config.json"
            config.write_text(json.dumps({"training_root": str(training), "profiles_root": str(profiles)}))
            with self.assertRaises(JobError):
                check_readiness(store, comfy, profiles, graph, config)
            for relative in (".venv/Scripts/python.exe", "trainer/train_identity_qwen21.py", "trainer/nf4.json",
                             "models/Qwen-Image-2.1/model_index.json"):
                target = training / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            result = check_readiness(store, comfy, profiles, graph, config)
            self.assertTrue(result["ready"])
            config.write_text(json.dumps({"training_root": str(training), "profiles_root": str(training / "other")}))
            with self.assertRaises(JobError):
                check_readiness(store, comfy, profiles, graph, config)

    def test_check_cli_never_locks_or_constructs_worker(self):
        graph, _, comfy, store = inputs()
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()) as output:
            with patch("services.worker.worker.FirebaseStore", return_value=store), \
                    patch("services.worker.worker.ComfyClient", return_value=comfy), \
                    patch("services.worker.worker.process_lock") as lock, \
                    patch("services.worker.worker.Worker") as worker:
                main(["--project", "test-project", "--bucket", "test-bucket", "--profiles-root", temp,
                      "--workflow", str(TEMPLATE), "--check"])
                lock.assert_not_called()
                worker.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())["ready"])
        store.expire_abandoned.assert_not_called()


if __name__ == "__main__":
    unittest.main()
