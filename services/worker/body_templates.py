"""Deterministic visual body-template selection from canonical metric measurements.

BMI is kg / metres squared. Version 2 raises all original visual cutoffs by 10%.
These are product heuristics for five illustration templates, not clinical BMI
categories, body-shape estimates or fit predictions. Saved identities retain the
policy that selected their original body so later scans use the same pose.
"""
from __future__ import annotations

import math
from pathlib import Path

try:
    from .comfy import safe_id
except ImportError:
    from comfy import safe_id


BODY_TEMPLATE_IDS = tuple(f"weight-{number}" for number in range(1, 6))  # Male set; IDs predate body styles.
FEMALE_BODY_TEMPLATE_IDS = tuple(f"female-weight-{number}" for number in range(1, 6))
ALL_BODY_TEMPLATE_IDS = BODY_TEMPLATE_IDS + FEMALE_BODY_TEMPLATE_IDS
BODY_STYLES = ("male", "female")
BODY_STYLE_TEMPLATE_IDS = {"male": BODY_TEMPLATE_IDS, "female": FEMALE_BODY_TEMPLATE_IDS}
BODY_TEMPLATE_POLICY_VERSION = "bmi-visual-v2"
BODY_TEMPLATE_POLICIES = {
    "bmi-visual-v1": (18.5, 22, 25, 30),
    "bmi-visual-v2": (20.35, 24.2, 27.5, 33.0),
}


def body_style(value):
    if value not in BODY_STYLES:
        raise ValueError("Choose male or female.")
    return value


def body_template_style(template_id):
    for style, ids in BODY_STYLE_TEMPLATE_IDS.items():
        if template_id in ids:
            return style
    raise ValueError("Unknown body template.")


def body_template_source(directory, number, style="male"):
    """Resolve a template image. Male: the original set, optionally renamed _Male (legacy name wins).

    Female: only the explicit Pose1_WeightN_Female.png file. The two sets never substitute for each other.
    The caller still validates that the returned path is a usable local image.
    """
    if type(number) is not int or not 1 <= number <= len(BODY_TEMPLATE_IDS):
        raise ValueError("Choose a body-template number between one and five.")
    directory = Path(directory)
    if body_style(style) == "female":
        return directory / f"Pose1_Weight{number}_Female.png"
    legacy = directory / f"Pose1_Weight{number}.png"
    return legacy if legacy.is_file() else directory / f"Pose1_Weight{number}_Male.png"


def select_body_template(height_cm, weight_kg, *, policy_version=BODY_TEMPLATE_POLICY_VERSION, style="male"):
    """Choose one template of the person's chosen body style; retain unrounded BMI and the input snapshot.

    Both styles use the same BMI ranges for their five numbered images. Male snapshots omit
    `bodyStyle`, so identities saved before body styles existed keep validating unchanged."""
    body_style(style)
    if not isinstance(policy_version, str) or policy_version not in BODY_TEMPLATE_POLICIES:
        raise ValueError("Choose a supported body-template policy version.")
    for name, value, low, high in (("height", height_cm, 80, 250), ("weight", weight_kg, 25, 300)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"Enter a valid {name} between {low} and {high} {'cm' if name == 'height' else 'kg'}.")
    height_cm, weight_kg = float(height_cm), float(weight_kg)
    bmi = weight_kg / (height_cm / 100) ** 2
    index = next((index for index, cutoff in enumerate(BODY_TEMPLATE_POLICIES[policy_version]) if bmi < cutoff), 4)
    snapshot = {"id": BODY_STYLE_TEMPLATE_IDS[style][index], "bmi": bmi, "heightCm": height_cm,
                "weightKg": weight_kg, "policyVersion": policy_version}
    if style != "male":
        snapshot["bodyStyle"] = style
    return snapshot


def body_catalog_paths(garment_id, garment, template_id):
    """Return the exact catalog inputs for a selected body, rejecting foreign or partial maps.

Legacy accounts deliberately use their existing base path without this helper. The male set
must be complete; the female set is optional but, when present, must also be complete, so an
incomplete rollout cannot choose a wrong body.
"""
    safe_id(garment_id)
    message = "This garment needs all five body templates configured by the project owner."
    if template_id not in ALL_BODY_TEMPLATE_IDS or not isinstance(garment, dict):
        raise ValueError(message)
    prefix = f"garments/{garment_id}"
    if garment.get("imagePath") != f"{prefix}/reference.png" or garment.get("baseImagePath") != f"{prefix}/base.png":
        raise ValueError(message)
    paths = garment.get("bodyBaseImagePaths")
    if (not isinstance(paths, dict) or not set(paths) <= set(ALL_BODY_TEMPLATE_IDS)
            or not set(BODY_TEMPLATE_IDS) <= set(paths)
            or len(set(paths) & set(FEMALE_BODY_TEMPLATE_IDS)) not in (0, len(FEMALE_BODY_TEMPLATE_IDS))
            or template_id not in paths):
        raise ValueError(message)
    for key in paths:
        permitted = [f"{prefix}/body-bases/{key}.png"]
        if key == "weight-3":
            permitted.append(f"{prefix}/base.png")
        if paths[key] not in permitted:
            raise ValueError(message)
    return {"imagePath": f"{prefix}/reference.png", "baseImagePath": paths[template_id]}
