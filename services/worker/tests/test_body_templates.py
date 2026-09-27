import math
import unittest

from services.worker.body_templates import BODY_TEMPLATE_IDS, BODY_TEMPLATE_POLICY_VERSION, body_catalog_paths, select_body_template


class BodyTemplatePolicyTests(unittest.TestCase):
    def test_every_boundary_uses_unrounded_bmi(self):
        # 200 cm gives an exact denominator of 4, isolating boundary behavior.
        for bmi, expected in ((18.4999, 1), (18.5, 2), (21.9999, 2), (22, 3),
                              (24.9999, 3), (25, 4), (29.9999, 4), (30, 5), (50, 5)):
            with self.subTest(bmi=bmi):
                result = select_body_template(200, bmi * 4)
                self.assertEqual(result["id"], f"weight-{expected}")
                self.assertEqual(result["bmi"], bmi)
                self.assertEqual(result["heightCm"], 200)
                self.assertEqual(result["weightKg"], bmi * 4)
                self.assertEqual(result["policyVersion"], BODY_TEMPLATE_POLICY_VERSION)

    def test_equivalent_us_and_metric_inputs_choose_the_same_template(self):
        height_cm = (5 * 12 + 10) * 2.54
        weight_kg = 150 * 0.45359237
        us = select_body_template(height_cm, weight_kg)
        metric = select_body_template(177.8, 68.0388555)
        self.assertEqual(us, metric)
        self.assertEqual(us["id"], "weight-2")
        self.assertAlmostEqual(us["bmi"], weight_kg / 1.778 ** 2, places=13)

    def test_finite_measurements_include_the_exact_allowed_endpoints(self):
        for height, weight in ((80, 25), (250, 300), (80, 300), (250, 25)):
            result = select_body_template(height, weight)
            self.assertTrue(math.isfinite(result["bmi"]))
            self.assertIn(result["id"], BODY_TEMPLATE_IDS)

    def test_invalid_measurements_never_silently_choose_a_body(self):
        for invalid in (True, False, None, "175", [], {}, float("nan"), float("inf"), -float("inf"), 10 ** 1000):
            for values in ((invalid, 70), (175, invalid)):
                with self.subTest(value=type(invalid).__name__, field=values[0] is invalid), self.assertRaises(ValueError):
                    select_body_template(*values)
        for values in ((79.99, 70), (250.01, 70), (175, 24.99), (175, 300.01)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                select_body_template(*values)


class BodyCatalogPathTests(unittest.TestCase):
    def garment(self):
        return {"imagePath": "garments/shirt/reference.png", "baseImagePath": "garments/shirt/base.png",
                "bodyBaseImagePaths": {key: f"garments/shirt/body-bases/{key}.png" for key in BODY_TEMPLATE_IDS}}

    def test_canonical_variants_and_explicit_legacy_middle_return_worker_path_keys(self):
        garment = self.garment()
        for key in BODY_TEMPLATE_IDS:
            self.assertEqual(body_catalog_paths("shirt", garment, key),
                             {"imagePath": "garments/shirt/reference.png", "baseImagePath": garment["bodyBaseImagePaths"][key]})
        garment["bodyBaseImagePaths"]["weight-3"] = garment["baseImagePath"]
        self.assertEqual(body_catalog_paths("shirt", garment, "weight-3")["baseImagePath"], "garments/shirt/base.png")

    def test_missing_partial_extra_or_foreign_maps_do_not_fall_back(self):
        invalid_maps = [None, {}, [], {"weight-3": "garments/shirt/base.png"},
                        self.garment()["bodyBaseImagePaths"] | {"weight-6": "garments/shirt/base.png"}]
        for mapping in invalid_maps:
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                body_catalog_paths("shirt", self.garment() | {"bodyBaseImagePaths": mapping}, "weight-3")
        for key in BODY_TEMPLATE_IDS:
            for path in ("users/alice/identity/private.png", "garments/other/body-bases/weight-1.png",
                         "../base.png", "https://example.invalid/base.png", "garments/shirt/body-bases/weight-9.png"):
                garment = self.garment(); garment["bodyBaseImagePaths"][key] = path
                with self.subTest(key=key, path=path), self.assertRaises(ValueError):
                    body_catalog_paths("shirt", garment, "weight-3")

    def test_only_middle_can_reuse_the_legacy_path(self):
        for key in ("weight-1", "weight-2", "weight-4", "weight-5"):
            garment = self.garment(); garment["bodyBaseImagePaths"][key] = garment["baseImagePath"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                body_catalog_paths("shirt", garment, key)

    def test_rejects_unknown_template_unsafe_id_and_foreign_reference(self):
        for key in (None, True, "weight-0", "weight-6", "../weight-1"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                body_catalog_paths("shirt", self.garment(), key)
        for garment_id in ("../shirt", "users/alice", None):
            with self.assertRaises(ValueError):
                body_catalog_paths(garment_id, self.garment(), "weight-3")
        for field in ("imagePath", "baseImagePath"):
            with self.assertRaises(ValueError):
                body_catalog_paths("shirt", self.garment() | {field: "garments/other/base.png"}, "weight-3")


if __name__ == "__main__":
    unittest.main()
