# Body templates selected from height and weight

New single-selfie onboarding selects one of the original five templates, now named `ClothesSwap/Pose1_WeightN_Male.png`. Renaming these files did not change the published image content or routing. The separate `_Female.png` files are not used by this rollout. The worker computes BMI from the saved metric values:

```text
BMI = weightKg / (heightCm / 100)²
```

The existing US inputs convert total feet/inches to centimetres with 2.54 and pounds to kilograms with 0.45359237. Selection uses unrounded BMI, so rounding the display cannot move somebody across a boundary. Invalid, non-finite or out-of-range measurements are rejected.

| Template | Source image | BMI range |
| --- | --- | --- |
| `weight-1` | `Pose1_Weight1_Male.png` | below 18.5 |
| `weight-2` | `Pose1_Weight2_Male.png` | 18.5 up to, but not including, 22 |
| `weight-3` | `Pose1_Weight3_Male.png` | 22 up to, but not including, 25 |
| `weight-4` | `Pose1_Weight4_Male.png` | 25 up to, but not including, 30 |
| `weight-5` | `Pose1_Weight5_Male.png` | 30 and above |

The [BMI calculation and thresholds 18.5, 25 and 30](https://www.cdc.gov/bmi/adult-calculator/bmi-categories.html) follow CDC documentation. The additional split at 22 is an application choice to distribute this particular five-image set. These are visual template ranges, not five clinical categories. BMI does not uniquely determine proportions, muscle mass, or clothing fit; the resulting image is an approximate appearance preview. The UI uses neutral wording and does not label the person's body or health.

## Identity and garment alignment

- The server chooses the template. Browser requests cannot submit a template ID, alternative file path, or model setting.
- The identity and private manifest store `bodyTemplate: {id, bmi, heightCm, weightKg, policyVersion: "bmi-visual-v1"}` when onboarding completes.
- Each garment render uses the same selected pose source as the personal base. All active garments must resolve to one pose hash for that selection, so onboarding still generates one personal base.
- Garment alignment allows collars and hems to cover the template's exposed neck or pants. It requires at least half of each original face and lower-body region to remain visible, and both visible regions must independently pass the 0.85 overlap threshold. Versioned mask caches recompute these checks on the CPU without rerendering clothing. Personal-base alignment checks remain unchanged.
- Every scan uses the saved identity's template snapshot. Editing profile measurements alone cannot composite a differently shaped garment onto the old body. **Update details and rebuild look** applies new measurements through the existing single-selfie flow.
- Existing identities without a body-template snapshot retain their original pose and cached garments until rebuilt.
- Version A's face pipeline stays at 24 steps, BFS strength 0.65, a 0.35 MP selfie reference, neutral expression and no close-up redraw. Output remains 1024 plus 2K on the fast path.

## Catalog and cache preparation

The operator publishes the five templates onto explicitly named existing garment entries:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = 'C:/private/firebase-worker-service-account.json'
$env:FIREBASE_PROJECT_ID = 'your-project-id'
$env:FIREBASE_STORAGE_BUCKET = 'your-project-id.firebasestorage.app'
services/worker/.venv/Scripts/python.exe -m services.worker.catalog seed-body-templates `
  --directory ClothesSwap demo-shirt thread-1 thread-2 thread-3
```

For each number, the command accepts the original `Pose1_WeightN.png` filename first, otherwise `Pose1_WeightN_Male.png`. If both exist, the original filename takes precedence. It never automatically substitutes a `_Female.png` file. The local pose utilities use this same resolution rule.

The command checks all five images and all target paths before writing. It creates metadata-stripped PNGs at `garments/{garmentId}/body-bases/weight-N.png`, or verifies identical existing bytes. When template 3 matches the existing `base.png`, its map entry reuses that path. Each garment gets a complete `bodyBaseImagePaths` map and a separate `bodyTemplatesUpdatedAt` timestamp. The garment's original paths and `updatedAt` remain unchanged, preserving the legacy cache keys. Repeating the same publication is a no-op.

With the live worker stopped and ComfyUI idle, prepare the shared cache using the same state directory as the live worker:

```powershell
services/worker/.venv/Scripts/python.exe services/worker/worker.py `
  --profiles-root comfy-identity/training/profiles --prewarm-only
```

Optional repeatable `--body-template weight-1` and `--garment thread-1` arguments restrict a preparation run. This mode holds the existing GPU process lock, prepares garment renders, fixed-pose upscales and masks, and exits without claiming user jobs. It reports a failure if any selected variant is unavailable. Restart the worker after preparation.

Five templates and four active garments require twenty cached combinations. Four existing template-3 renders can be reused in this deployment; sixteen new renders are a one-time setup cost. Onboarding and scans require the matching render to be ready instead of unexpectedly starting several minutes of garment generation inside a user's request. Local models, source photos and bulk renders stay out of Git.
