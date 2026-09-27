"""Male/female body styles: selection, catalog maps, publication and saved-identity compatibility."""
from pathlib import Path
import unittest

from PIL import Image

from services.worker.body_templates import (BODY_TEMPLATE_IDS, FEMALE_BODY_TEMPLATE_IDS, body_catalog_paths,
                                            body_template_source, select_body_template)
from services.worker.catalog import normalized_png
from services.worker.worker import JobError, configured_body_templates, saved_body_template

from test_catalog_body_templates import BodyTemplatePublicationTests


class BodyStyleSelectionTests(unittest.TestCase):
    def test_same_bmi_ranges_pick_the_matching_numbered_image_in_each_style(self):
        male = select_body_template(172.72, 77.1107029)
        female = select_body_template(172.72, 77.1107029, style="female")
        self.assertEqual((male["id"], female["id"]), ("weight-3", "female-weight-3"))
        self.assertEqual(female["bmi"], male["bmi"])
        self.assertNotIn("bodyStyle", male)                 # Male snapshots stay exactly as before.
        self.assertEqual(female["bodyStyle"], "female")
        for height, weight, index in ((180, 55, 1), (170, 65, 2), (165, 95, 5)):
            self.assertEqual(select_body_template(height, weight, style="female")["id"], f"female-weight-{index}")
        with self.assertRaises(ValueError):
            select_body_template(170, 65, style="other")

    def test_saved_snapshots_validate_in_their_own_style(self):
        female = select_body_template(165, 60, style="female")
        self.assertEqual(saved_body_template({"bodyTemplate": female}), female["id"])
        legacy_male = select_body_template(180, 80)          # Saved before body styles existed.
        self.assertEqual(saved_body_template({"bodyTemplate": legacy_male}), legacy_male["id"])
        with self.assertRaises(JobError):                    # A female ID claimed without the female style.
            saved_body_template({"bodyTemplate": female | {"bodyStyle": "male"}})

    def test_female_sources_never_substitute_for_male_ones(self):
        directory = Path("ClothesSwap")
        self.assertEqual(body_template_source(directory, 2, "female").name, "Pose1_Weight2_Female.png")
        self.assertIn(body_template_source(directory, 2).name, ("Pose1_Weight2.png", "Pose1_Weight2_Male.png"))


class BodyStyleCatalogTests(unittest.TestCase):
    def garment(self, female=True):
        paths = {key: f"garments/shirt/body-bases/{key}.png" for key in BODY_TEMPLATE_IDS}
        if female:
            paths |= {key: f"garments/shirt/body-bases/{key}.png" for key in FEMALE_BODY_TEMPLATE_IDS}
        return {"imagePath": "garments/shirt/reference.png", "baseImagePath": "garments/shirt/base.png", "bodyBaseImagePaths": paths}

    def test_female_set_is_optional_but_complete_when_present(self):
        self.assertEqual(body_catalog_paths("shirt", self.garment(), "female-weight-4")["baseImagePath"],
                         "garments/shirt/body-bases/female-weight-4.png")
        with self.assertRaises(ValueError):                  # Not seeded for this garment yet.
            body_catalog_paths("shirt", self.garment(female=False), "female-weight-4")
        partial = self.garment(); del partial["bodyBaseImagePaths"]["female-weight-5"]
        with self.assertRaises(ValueError):
            body_catalog_paths("shirt", partial, "weight-3")
        reused = self.garment(); reused["bodyBaseImagePaths"]["female-weight-3"] = "garments/shirt/base.png"
        with self.assertRaises(ValueError):                  # Only the male middle may reuse the legacy base.
            body_catalog_paths("shirt", reused, "female-weight-3")
        self.assertEqual(configured_body_templates(self.garment()), BODY_TEMPLATE_IDS + FEMALE_BODY_TEMPLATE_IDS)
        self.assertEqual(configured_body_templates(self.garment(female=False)), BODY_TEMPLATE_IDS)


class FemalePublicationTests(unittest.TestCase):
    """Reuses the male publication fixture (in-memory bucket and catalog) without re-running its tests."""
    seed = BodyTemplatePublicationTests.seed

    def setUp(self):
        BodyTemplatePublicationTests.setUp(self)
        for index in range(1, 6):
            Image.new("RGB", (8, 8), (10, index * 35, 200)).save(self.directory / f"Pose1_Weight{index}_Female.png")

    def test_female_publication_adds_to_the_male_map(self):
        from services.worker.catalog import seed_body_templates
        self.seed()
        male_paths = dict(self.db.documents["shirt"]["bodyBaseImagePaths"])
        result = seed_body_templates(self.db, self.bucket, directory=self.directory, garment_ids=("shirt",), style="female")
        paths = self.db.documents["shirt"]["bodyBaseImagePaths"]
        self.assertEqual(result["uploadedImages"], 5)
        self.assertEqual({key: paths[key] for key in male_paths}, male_paths)
        for index, key in enumerate(FEMALE_BODY_TEMPLATE_IDS, 1):
            self.assertEqual(paths[key], f"garments/shirt/body-bases/{key}.png")
            self.assertEqual(self.bucket.objects[paths[key]], normalized_png(self.directory / f"Pose1_Weight{index}_Female.png")[0])
        again = seed_body_templates(self.db, self.bucket, directory=self.directory, garment_ids=("shirt",), style="female")
        self.assertEqual((again["uploadedImages"], again["updatedGarments"]), (0, 0))

    def test_female_publication_requires_the_male_set_first(self):
        from services.worker.catalog import seed_body_templates
        with self.assertRaises(ValueError):
            seed_body_templates(self.db, self.bucket, directory=self.directory, garment_ids=("shirt",), style="female")
        self.assertEqual(self.bucket.uploads, [])


if __name__ == "__main__":
    unittest.main()
