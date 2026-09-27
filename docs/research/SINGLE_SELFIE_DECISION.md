# Production decision: keep one selfie and version A

Decision recorded September 26, 2026 (America/New_York), following the owner's controlled Likeness Lab comparison. Production continues to accept one current-look selfie, taken with the camera or chosen from the photo library. Left and right angle photos are not required or used by production onboarding.

## Evidence

The private local session `39c55dd42030425da0ee84992f821e52` used angles mode from commit `11fd07d`; session links and restart recovery were corrected in `31f3e8b`. Its saved results are:

| Display label | Method | Saved onboarding-stage time |
| --- | --- | --- |
| 2 | A: current production, one selfie | 54.2 s |
| 1 | F: A's settings and seed, plus two angle photos | 83.6 s |

The owner described the results as almost identical and slightly preferred A. The extra photos added 29.4 seconds in this run without a likeness improvement worth the added capture effort. These are lab-stage timings, not phone-to-result measurements. This is one person's comparison, not evidence that side views never help anyone.

The earlier session `ec86054a00b8436b83a3a43d2f2d1800` called A **Version 3** because display labels were shuffled. That accepted version also used only the primary selfie. The controlled A/F comparison resolves the earlier assumption that the accepted result depended on the side photos.

## Production settings retained

- Qwen Image 2.1 Q8 with BFS head-swap LoRA strength 0.65.
- Reference-only identity, strength 0.8; no person-specific trained LoRA.
- Primary selfie reference at 0.35 MP; runtime sampling at 24 steps.
- Neutral expression and the existing person-neutral hair, covering, and exposed-skin instructions.
- Texture pass bypassed; no close-up redraw or HyperSwap polish.
- Existing personal-base pose locking, masks, cached garment renders, and CPU scan compositing.

The graph's stored sampling default is overridden by the worker. Keep `PERSONAL_STEPS` and `patch_personal_graph` consistent with this accepted version; do not promote lab variants automatically.

## Disposition of unfinished work

The uncommitted three-angle camera component, browser face-model dependency, three-photo upload/rules contract, and optional worker angle conditioning were removed from the working tree. A recovery copy remains under ignored `.local/`. They were never deployed, so this decision requires no production rollback or account reset. The consultant's committed lab comparison and link fixes remain in the repository.

Original photos, generated comparisons, and participant records remain in ignored `comfy-identity/likeness-lab-sessions/`; none are published with this decision note.
