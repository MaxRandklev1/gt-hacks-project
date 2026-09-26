import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from services.worker.comfy import ComfyClient, ComfyError, LocalProfiles, contained, patch_graph


ROOT = Path(__file__).resolve().parents[3]


class ComfyTests(unittest.TestCase):
    def test_reject_remote_proxy_credentials_and_redirects(self):
        for url in ["https://127.0.0.1:8188", "http://localhost:8188", "http://example.com",
                    "http://127.0.0.1:8188@evil.test", "http://127.0.0.1:8188/remote", "http://user:pass@127.0.0.1"]:
            with self.assertRaises(ValueError):
                ComfyClient(url)
        session = Mock()
        session.headers = {}
        session.request.return_value.status_code = 302
        client = ComfyClient(session=session)
        with self.assertRaises(ComfyError):
            client.request("GET", "/queue")
        self.assertFalse(session.trust_env)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])

    def test_training_always_400_and_mutation_header(self):
        session = Mock()
        session.headers = {}
        session.request.return_value.status_code = 202
        session.request.return_value.json.return_value = {"job": {"job_id": "local-1"}}
        client = ComfyClient(session=session)
        self.assertEqual(client.train("cloud-profile"), {"job_id": "local-1"})
        self.assertEqual(session.headers["X-Universal-Identity"], "1")
        self.assertEqual(session.request.call_args.kwargs["json"], {"steps": 400})

    def test_graph_changes_only_reviewed_data_inputs(self):
        path = ROOT / "comfy-identity/Qwen21_Universal_TryOn_4K.api.json"
        template = json.loads(path.read_text(encoding="utf-8-sig"))
        untouched = copy.deepcopy(template)
        graph = patch_graph(template, profile_id="owner-profile", base_image="cloud_base.png",
                            garment_image="cloud_garment.png", job_id="job1")
        self.assertEqual(template, untouched)
        self.assertEqual(graph["14"]["inputs"]["profile_id"], "owner-profile")
        self.assertEqual(graph["14"]["inputs"]["reference_index"], 0)
        self.assertTrue(graph["14"]["inputs"]["use_trained_identity"])
        self.assertEqual(graph["1"]["inputs"]["image"], "cloud_base.png")
        self.assertEqual(graph["32"]["inputs"]["image"], "cloud_garment.png")
        for key, value in template.items():
            if key not in {"1", "14", "32"} and value["class_type"] != "SaveImage":
                self.assertEqual(graph[key], value)
        self.assertEqual(graph["35"]["inputs"]["images"], ["34", 0])
        with self.assertRaises(ValueError):
            patch_graph(template, profile_id="../../other", base_image="a.png", garment_image="b.png", job_id="j")

    def test_local_profile_owner_and_restore_keep_original_trigger(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "new-profile"
            directory.mkdir()
            (directory / "profile.json").write_text(json.dumps({"id": "new-profile", "trigger": "new_token",
                "photos": [{"id": "p0000", "caption": "photo of new_token"}]}))
            profiles = LocalProfiles(root)
            profiles.restore("new-profile", "alice", {"version": "training1", "trigger": "original_person"}, b"fake-weights")
            path, profile = profiles.adapter("new-profile", "alice", "training1")
            self.assertEqual(path.read_bytes(), b"fake-weights")
            self.assertEqual(profile["trigger"], "original_person")
            self.assertIn("original_person", profile["photos"][0]["caption"])
            with self.assertRaises(ValueError):
                profiles.adapter("new-profile", "bob", "training1")
            with self.assertRaises(ValueError):
                profiles.adapter("new-profile", "alice", "other-version")
            with self.assertRaises(ValueError):
                contained(directory, "../outside.safetensors")

    def test_both_upscale_graphs_remove_alpha_before_three_channel_model(self):
        # A real RGBA composited result failed inside the three-channel upscaler.
        # Keep the normalization boundary in both full and standalone API exports.
        for stem, source, rgb, upscale, save in [
                ("Qwen21_Universal_TryOn_4K", "29", "37", "34", "35"),
                ("Qwen21_TryOn_Upscale_Only", "1", "6", "3", "4")]:
            with self.subTest(workflow=stem):
                graph = json.loads((ROOT / "comfy-identity" / (stem + ".api.json")).read_text())
                self.assertEqual(graph[rgb]["class_type"], "SplitImageWithAlpha")
                self.assertEqual(graph[rgb]["inputs"]["image"], [source, 0])
                self.assertEqual(graph[upscale]["inputs"]["image"], [rgb, 0])
                self.assertEqual(graph[save]["inputs"]["images"], [upscale, 0])


if __name__ == "__main__":
    unittest.main()
