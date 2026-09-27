"""Deterministic visual body-template selection from canonical metric measurements.

BMI is kg / metres squared. The extra split at 22 is a product heuristic for five
illustration templates, not a clinical BMI category, body-shape estimate or fit prediction.
Reference: https://www.cdc.gov/bmi/adult-calculator/bmi-categories.html
"""
from __future__ import annotations

import math
from pathlib import Path

try:
    from .comfy import safe_id
except ImportError:
    from comfy import safe_id


BODY_TEMPLATE_IDS = tuple(f"weight-{number}" for number in range(1, 6))
BODY_TEMPLATE_POLICY_VERSION = "bmi-visual-v1"


def body_template_source(directory, number):
    """Resolve the original template set after its optional _Male filename rename.

    The legacy name wins if both exist. Never auto-select the separate _Female set.
    The caller still validates that the returned path is a usable local image.
    """
    if type(number) is not int or not 1 <= number <= len(BODY_TEMPLATE_IDS):
        raise ValueError("Choose a body-template number between one and five.")
    directory = Path(directory)
    legacy = directory / f"Pose1_Weight{number}.png"
    return legacy if legacy.is_file() else directory / f"Pose1_Weight{number}_Male.png"


def select_body_template(height_cm, weight_kg):
    """Choose one template; retain unrounded BMI and the input measurement snapshot."""
    for name, value, low, high in (("height", height_cm, 80, 250), ("weight", weight_kg, 25, 300)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"Enter a valid {name} between {low} and {high} {'cm' if name == 'height' else 'kg'}.")
    height_cm, weight_kg = float(height_cm), float(weight_kg)
    bmi = weight_kg / (height_cm / 100) ** 2
    index = next((index for index, cutoff in enumerate((18.5, 22, 25, 30)) if bmi < cutoff), 4)
    return {"id": BODY_TEMPLATE_IDS[index], "bmi": bmi, "heightCm": height_cm,
            "weightKg": weight_kg, "policyVersion": BODY_TEMPLATE_POLICY_VERSION}


def body_catalog_paths(garment_id, garment, template_id):
    """Return the exact catalog inputs for a selected body, rejecting foreign or partial maps.

Legacy accounts deliberately use their existing base path without this helper. New
accounts require all five templates so an incomplete rollout cannot choose a wrong body.
"""
    safe_id(garment_id)
    message = "This garment needs all five body templates configured by the project owner."
    if template_id not in BODY_TEMPLATE_IDS or not isinstance(garment, dict):
        raise ValueError(message)
    prefix = f"garments/{garment_id}"
    if garment.get("imagePath") != f"{prefix}/reference.png" or garment.get("baseImagePath") != f"{prefix}/base.png":
        raise ValueError(message)
    paths = garment.get("bodyBaseImagePaths")
    if not isinstance(paths, dict) or set(paths) != set(BODY_TEMPLATE_IDS):
        raise ValueError(message)
    for key in BODY_TEMPLATE_IDS:
        permitted = [f"{prefix}/body-bases/{key}.png"]
        if key == "weight-3":
            permitted.append(f"{prefix}/base.png")
        if paths[key] not in permitted:
            raise ValueError(message)
    return {"imagePath": f"{prefix}/reference.png", "baseImagePath": paths[template_id]}
