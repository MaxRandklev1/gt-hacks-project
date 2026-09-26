from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import uuid

from PIL import Image

from services.worker.comfy import patch_swap_graph
from services.worker.worker import JobError, Worker, faceswap_paths, now, sha256, validate_job


ROOT = Path(__file__).resolve().parents[3]
SWAP = json.loads((ROOT / "comfy-identity/FaceSwap_TryOn_2K.api.json").read_text())


def png(size=(16, 16), color="navy"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def enroll_job(**extra):
    upload, stamp = str(uuid.uuid4()), now()
    return {"uid": "alice", "kind": "enroll", "requestVersion": 5, "uploadId": upload, "createdAt": stamp,
            "selfiePath": f"users/alice/uploads/{upload}/selfie.jpg", "selfieSource": "upload",
            "selfieSelectedAt": stamp} | extra


def ready_identity(reference=png()):
    return {"status": "ready", "mode": "faceswap", "version": "enroll1", **faceswap_paths("alice", "enroll1"),
            "referenceSha256": sha256(reference)}


class EnrollContractTests(unittest.TestCase):
    def test_selfie_only_request_contract(self):
        job = enroll_job()
        self.assertEqual(validate_job("enroll1", job), "alice")
        camera = job | {"selfieSource": "camera", "selfieCapturedAt": job["selfieSelectedAt"]}
        self.assertEqual(validate_job("enroll1", camera), "alice")
        self.assertEqual(validate_job("gen1", {"uid": "alice", "kind": "generate", "requestVersion": 5, "garmentId": "shirt"}), "alice")
        for altered in [job | {"requestVersion": 4}, job | {"photoPaths": []},
                        job | {"selfiePath": job["selfiePath"].replace("alice", "bob")},
                        job | {"selfieSelectedAt": job["createdAt"] - timedelta(hours=2)},
                        job | {"selfieCapturedAt": job["selfieSelectedAt"]},
                        job | {"kind": "train"}, job | {"kind": "finalize"}]:
            with self.subTest(altered=altered), self.assertRaises(JobError):
                validate_job("enroll1", altered)
        with self.assertRaises(ValueError):
            validate_job("gen1", {"uid": "alice", "kind": "generate", "requestVersion": 5, "garmentId": "../x"})

    def test_swap_graph_is_local_and_patches_only_images(self):
        graph = patch_swap_graph(SWAP, selfie_image="cloud_a.png", styled_image="cloud_b.png", styled_2k_image="cloud_c.png", job_id="gen1")
        self.assertEqual([graph[n]["inputs"]["image"] for n in "123"], ["cloud_a.png", "cloud_b.png", "cloud_c.png"])
        self.assertEqual(graph["4"]["inputs"], SWAP["4"]["inputs"])
        remote = json.loads(json.dumps(SWAP))
        remote["5"]["inputs"]["api_token"] = "secret"
        for bad in [dict(template=remote), dict(selfie_image="../x.png")]:
            arguments = dict(template=SWAP, selfie_image="a.png", styled_image="b.png", styled_2k_image="c.png", job_id="gen1") | bad
            with self.subTest(bad=list(bad)), self.assertRaises(ValueError):
                patch_swap_graph(**arguments)


class FaceSwapWorkerTests(unittest.TestCase):
    def make(self, temp):
        store, comfy, lease = Mock(), Mock(), Mock()
        worker = Worker(store, comfy, Mock(), {}, Path(temp) / "state", selfie_validator=Mock())
        return worker, store, comfy, lease

    def test_enroll_publishes_selfie_identity_without_training(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy, lease = self.make(temp)
            store.download.return_value = png()
            user = {"heightCm": 180, "weightKg": 75, "measurementSystem": "us"}
            worker.enroll("enroll1", enroll_job(), "alice", user, Path(temp), lease)
            self.assertEqual([call.args[0] for call in store.upload.call_args_list],
                             ["users/alice/identity/enroll1/reference.png", "users/alice/identity/enroll1/manifest.json"])
            manifest = json.loads(store.upload.call_args_list[1].args[1])
            self.assertEqual((manifest["schemaVersion"], manifest["mode"], manifest["uid"]), (4, "faceswap", "alice"))
            identity = lease.write.call_args.kwargs["user_fields"]["identity"]
            self.assertEqual((identity["status"], identity["mode"], identity["referenceSha256"]), ("ready", "faceswap", manifest["referenceSha256"]))
            comfy.create_profile.assert_not_called()
            comfy.train.assert_not_called()
            comfy.submit.assert_not_called()

    def test_enroll_requires_measurements_and_a_usable_face(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, _, lease = self.make(temp)
            with self.assertRaises(JobError):
                worker.enroll("enroll1", enroll_job(), "alice", {}, Path(temp), lease)
            store.download.return_value = png()
            worker.selfie_validator.side_effect = ValueError("No clear face.")
            with self.assertRaises(JobError):
                worker.enroll("enroll1", enroll_job(), "alice", {"heightCm": 180, "weightKg": 75, "measurementSystem": "us"}, Path(temp), lease)
            store.upload.assert_not_called()

    def test_foreign_identity_paths_fail_before_download(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, _, _ = self.make(temp)
            foreign = ready_identity() | {"referencePath": "users/bob/identity/enroll1/reference.png"}
            with self.assertRaises(JobError):
                worker.identity_reference("alice", foreign, Path(temp))
            with self.assertRaises(JobError):
                worker.identity_reference("alice", {"status": "failed"}, Path(temp))
            store.download.assert_not_called()

    def test_tampered_reference_fails_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, _, _ = self.make(temp)
            identity = ready_identity()
            manifest = {"uid": "alice", "version": "enroll1", **faceswap_paths("alice", "enroll1"), "referenceSha256": identity["referenceSha256"]}
            store.download.side_effect = [json.dumps(manifest).encode(), png(color="red")]
            with self.assertRaises(JobError):
                worker.identity_reference("alice", identity, Path(temp))

    def test_first_scan_renders_garment_once_then_swaps_in_one_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store, comfy, lease = self.make(temp)
            reference = png()
            identity = ready_identity(reference)
            manifest = {"uid": "alice", "version": "enroll1", **faceswap_paths("alice", "enroll1"), "referenceSha256": identity["referenceSha256"]}
            garment = {"active": True, "name": "Dragon tee", "imagePath": "garments/shirt/reference.png",
                       "baseImagePath": "garments/shirt/base.png", "updatedAt": now()}
            store.garment.return_value = garment
            store.download.side_effect = [json.dumps(manifest).encode(), reference, png(), png()]
            comfy.upload_image.side_effect = lambda path: "cloud_" + uuid.uuid4().hex + ".png"
            comfy.submit.side_effect = ["styled-prompt", "swap-1", "swap-2"]
            comfy.output.side_effect = [png((16, 16)), png((32, 32)), png((16, 16)), png((32, 32)),
                                        png((16, 16)), png((32, 32))]
            worker.generate("gen1", {"garmentId": "shirt"}, "alice", {"identity": identity}, Path(temp), lease)
            self.assertEqual(comfy.submit.call_count, 2)
            self.assertEqual([call.args[1] for call in comfy.output.call_args_list], ["24", "35", "6", "7"])
            final = lease.write.call_args
            self.assertEqual(final.args[0]["status"], "completed")
            history = final.kwargs["generation"]
            self.assertEqual((history["imagePath"], history["image2kPath"], history["width2k"]),
                             ("users/alice/generations/gen1/result.png", "users/alice/generations/gen1/result-2k.jpg", 32))
            self.assertEqual(sorted(call.args[2] for call in store.upload.call_args_list), ["image/jpeg", "image/png"])
            # A second scan reuses the cached render, the uploaded Comfy inputs and the cached selfie.
            store.download.reset_mock()
            store.download.side_effect = None
            uploads = comfy.upload_image.call_count
            worker.generate("gen2", {"garmentId": "shirt"}, "alice", {"identity": identity}, Path(temp), lease)
            self.assertEqual(comfy.submit.call_count, 3)
            self.assertEqual(comfy.upload_image.call_count, uploads + 1)  # Only the selfie.
            store.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
