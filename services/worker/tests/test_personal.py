import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from services.worker.compose import compose, garment_mask, hair_matte, head_crop, lock_personal_base
from services.worker.parsing import LABELS, group_mask
from services.worker.personal import CompositeError, personal_paths, png_bytes, mask_png, sha256
from services.worker.worker import FirebaseStore, JobError, Worker, now


L = {name: index for index, name in enumerate(LABELS)}


def figure(size=200, face_offset=0, printed_face=False):
    """Synthetic label map: face, hair, tee, arms and pants in a fixed pose."""
    labels = np.zeros((size, size), np.uint8)
    labels[20:50, 85 + face_offset:115 + face_offset] = L["face"]
    labels[10:20, 85 + face_offset:115 + face_offset] = L["hair"]
    labels[55:120, 60:140] = L["upper_clothes"]
    labels[55:120, 45:60] = L["right_arm"]
    labels[55:120, 140:155] = L["left_arm"]
    labels[120:190, 65:135] = L["pants"]
    if printed_face:  # A photograph printed on the shirt.
        labels[80:95, 90:105] = L["face"]
        labels[76:80, 90:105] = L["hair"]
    return labels


class ComposeTests(unittest.TestCase):
    def test_printed_faces_stay_part_of_the_garment(self):
        base = figure()
        from services.worker.compose import base_regions
        mask = garment_mask(figure(printed_face=True), base_regions(base))
        self.assertTrue(mask[85, 95])          # Printed face is garment.
        self.assertFalse(mask[30, 100])        # Real face is not.
        self.assertFalse(mask[80, 50])         # Real arm is not.
        self.assertFalse(mask[150, 100])       # Pants are not.

    def test_head_crop_rejects_selfies_without_a_face(self):
        image = Image.new("RGB", (200, 200))
        with self.assertRaises(CompositeError):
            head_crop(image, np.zeros((200, 200), np.uint8))
        crop = head_crop(image, figure())
        self.assertLess(crop.width, 200)

    def test_locking_keeps_pose_pixels_outside_the_edit_region(self):
        base, result = Image.new("RGB", (64, 64), (200, 200, 200)), Image.new("RGB", (64, 64), (10, 10, 10))
        edit = np.zeros((64, 64), bool); edit[:16] = True
        locked = np.asarray(lock_personal_base(base, result, edit))
        self.assertEqual(tuple(locked[60, 30]), (200, 200, 200))
        self.assertEqual(tuple(locked[4, 30]), (10, 10, 10))

    def test_hair_over_the_garment_keeps_hair_and_replaces_the_tee_behind_it(self):
        size = 64
        tee, shirt, hair_colour = (240, 240, 240), (30, 60, 200), (40, 25, 15)
        pose = Image.new("RGB", (size, size), tee)
        personal = np.array(pose)
        hair = np.zeros((size, size), bool); hair[:, 20:28] = True
        personal[hair] = hair_colour
        render = Image.new("RGB", (size, size), shirt)
        garment = np.ones((size, size), bool)
        out = np.asarray(compose(Image.fromarray(personal), render, garment, hair, None, pose, None, np.ones((size, size), bool)))
        self.assertLess(np.abs(out[32, 24].astype(int) - hair_colour).max(), 12)   # Hair kept.
        self.assertLess(np.abs(out[32, 40].astype(int) - shirt).max(), 3)          # Tee replaced.
        # No white tee fringe beside the hair.
        self.assertLess(out[32, 29].astype(int).mean(), 200)
        self.assertGreater(float(hair_matte(Image.fromarray(personal), pose, hair)[32, 24, 0]), 0.9)


    def test_close_up_box_is_square_inside_the_image_and_blend_fades_at_its_border(self):
        from services.worker.compose import blend_face, face_box
        box = face_box(figure())
        self.assertEqual(box[2] - box[0], box[3] - box[1])
        self.assertTrue(0 <= box[0] and 0 <= box[1] and box[2] <= 200 and box[3] <= 200)
        image = Image.new("RGB", (200, 200), (0, 0, 0))
        mask = np.ones((512, 512), bool)
        out = np.asarray(blend_face(image, Image.new("RGB", (512, 512), (255, 255, 255)), box, mask))
        centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        self.assertGreater(out[centre[1], centre[0]].mean(), 240)       # Redrawn head used inside.
        self.assertLess(out[box[1], box[0]].mean(), 5)                  # Crop edge never shows.
        with self.assertRaises(CompositeError):
            face_box(np.zeros((50, 50), np.uint8))


    def test_arm_skin_tone_moves_to_the_face_but_keeps_its_own_texture(self):
        from services.worker.compose import match_skin_tone, skin_tone_transform
        labels = np.zeros((200, 200), np.uint8)
        labels[20:60, 80:120] = L["face"]
        labels[70:190, 20:50] = L["right_arm"]
        rng = np.random.default_rng(0)
        image = np.full((200, 200, 3), 235, np.uint8)
        image[20:60, 80:120] = (120, 80, 55)                                   # Brown face.
        texture = rng.integers(-12, 12, (120, 30, 1))
        image[70:190, 20:50] = np.clip(np.array([225, 190, 170]) + texture, 0, 255)  # Light, textured arm.
        picture = Image.fromarray(image)
        tone = skin_tone_transform(picture, labels)
        self.assertIsNotNone(tone)
        out = np.asarray(match_skin_tone(picture, group_mask(labels, "arms"), tone)).astype(int)
        arm = out[90:170, 28:42]
        self.assertLess(np.abs(arm.reshape(-1, 3).mean(0) - (120, 80, 55)).max(), 15)   # Now brown like the face.
        self.assertGreater(arm.std(), 3)                                                # Texture kept, not a flat fill.
        self.assertEqual(out[5, 150].tolist(), [235, 235, 235])                        # Background untouched.
        # Arms that already match, or a missing face, are left alone.
        image[70:190, 20:50] = np.clip(np.array([120, 80, 55]) + texture, 0, 255)
        self.assertIsNone(skin_tone_transform(Image.fromarray(image), labels))
        self.assertIsNone(skin_tone_transform(picture, np.where(labels == L["face"], 0, labels).astype(np.uint8)))


    def test_uncovered_tee_is_filled_from_what_the_render_shows(self):
        from services.worker.compose import fill_uncovered
        size = 64
        personal = Image.new("RGB", (size, size), (240, 240, 240))          # White tee left visible.
        render = np.full((size, size, 3), 200, np.uint8)
        render[:, :32] = (150, 110, 90)                                       # Render: model's neck skin on the left...
        uncovered = np.zeros((size, size), bool); uncovered[20:40, 10:54] = True
        skin = np.zeros((size, size), bool); skin[:, :32] = True
        background = ~skin                                                    # ...and background on the right.
        tone = {"render": np.array([130.0, 140.0, 145.0]), "person": np.array([90.0, 145.0, 150.0]), "ratio": np.ones(3)}
        out = fill_uncovered(np.asarray(personal, np.float32), personal, Image.fromarray(render), uncovered, skin, background,
                             tone, np.zeros((size, size), bool), np.zeros((size, size), bool), 1)
        self.assertLess(out[30, 15].mean(), 150)                             # Skin, shifted darker to the person.
        self.assertLess(abs(out[30, 50].mean() - 240), 12)                   # Background matched to the personal base.
        self.assertEqual(out[5, 5].tolist(), [240, 240, 240])                # Outside the uncovered area: untouched.


class PersonalWorkerTests(unittest.TestCase):
    def make(self, temp):
        store, comfy = Mock(), Mock()
        return Worker(store, comfy, Mock(), {}, Path(temp) / "state", selfie_validator=Mock()), store

    def identity(self, assets):
        paths = personal_paths("alice", "enroll1", "pose1")
        record = paths | {name + "Sha256": sha256(data) for name, data in assets.items()}
        return {"status": "ready", "mode": "personal_base", "version": "enroll1", "personalBases": {"pose1": record}}

    def assets(self):
        return {"p1024": png_bytes(Image.new("RGB", (16, 16))), "p2k": png_bytes(Image.new("RGB", (32, 32))),
                "hair": mask_png(np.zeros((16, 16), bool)), "tee": mask_png(np.zeros((16, 16), bool))}

    def test_personal_assets_download_once_and_verify_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store = self.make(temp)
            assets = self.assets()
            paths = personal_paths("alice", "enroll1", "pose1")
            store.download.side_effect = lambda path, limit: {paths[k]: v for k, v in assets.items()}[path]
            identity = self.identity(assets)
            self.assertEqual(worker.personal_assets("alice", identity, "pose1")["p2k"].size, (32, 32))
            store.download.reset_mock()
            worker.personal_assets("alice", identity, "pose1")
            store.download.assert_not_called()  # Local cache.
            tampered = self.identity(assets | {"p2k": b"other"})
            with self.assertRaises(CompositeError):
                worker.personal_assets("alice", tampered, "pose1")

    def test_foreign_or_missing_personal_base_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store = self.make(temp)
            identity = self.identity(self.assets())
            identity["personalBases"]["pose1"]["p1024"] = "users/bob/identity/enroll1/personal-pose1-1024.png"
            for bad, pose in [(identity, "pose1"), (self.identity(self.assets()), "other-pose")]:
                with self.assertRaises(CompositeError):
                    worker.personal_assets("alice", bad, pose)
            store.download.assert_not_called()

    def test_personal_try_on_never_calls_comfy(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store = self.make(temp)
            lease = Mock()
            store.garment.return_value = {"active": True, "name": "Tee", "imagePath": "garments/shirt/reference.png",
                                          "baseImagePath": "garments/shirt/base.png", "updatedAt": now()}
            worker.styled_garment = Mock(return_value=("render1", ("a.png", "b.png"), "pose1"))
            worker.personal_assets = Mock(return_value={})
            worker.garment_masks = Mock(return_value={})
            worker.composite_tryon = Mock(return_value=(Image.new("RGB", (16, 16)), Image.new("RGB", (32, 32))))
            worker.generate("gen1", {"garmentId": "shirt"}, "alice", {"identity": self.identity(self.assets())}, Path(temp), lease)
            worker.comfy.submit.assert_not_called()
            history = lease.write.call_args.kwargs["generation"]
            self.assertEqual((history["status"], history["image2kPath"], history["width2k"]), ("completed", "users/alice/generations/gen1/result-2k.jpg", 32))

    def test_misaligned_garment_render_fails_with_guidance(self):
        with tempfile.TemporaryDirectory() as temp:
            worker, store = self.make(temp)
            store.garment.return_value = {"active": True, "name": "Tee", "imagePath": "garments/shirt/reference.png",
                                          "baseImagePath": "garments/shirt/base.png", "updatedAt": now()}
            worker.styled_garment = Mock(return_value=("render1", ("a.png", "b.png"), "pose1"))
            worker.personal_assets = Mock(return_value={})
            worker.garment_masks = Mock(side_effect=ValueError("misaligned"))
            with self.assertRaises(JobError):
                worker.generate("gen1", {"garmentId": "shirt"}, "alice", {"identity": self.identity(self.assets())}, Path(temp), Mock())


class LaneTests(unittest.TestCase):
    def test_cpu_lane_leaves_gpu_jobs_queued(self):
        from test_worker import fake_store
        store = fake_store({"jobs/enroll1": {"uid": "alice", "kind": "enroll", "requestVersion": 5, "status": "queued"},
                            "jobs/gen1": {"uid": "alice", "kind": "generate", "requestVersion": 5, "status": "queued", "garmentId": "shirt"}})
        claimed = store.claim("worker-cpu", {"generate"})
        self.assertEqual(claimed[0], "gen1")
        self.assertEqual(store.db.data["jobs/enroll1"]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
