# Likeness Lab

A local page for judging likeness by eye. Upload a selfie, get several versions of yourself in the same garment under shuffled labels, pick the one that looks most like you (or "none"), and note what's off. The versions are revealed after you pick. No computer likeness score is involved: the person in the photo is the judge.

```powershell
.\scripts\start-likeness-lab.ps1      # then open http://127.0.0.1:8765
```

It needs ComfyUI running, plus the worker's cached garment renders and pose base (`.local/firebase-worker/styled/` and `pose/`, created by the worker's startup and first onboarding). Run it when nobody is using the live demo: it shares the GPU. One comparison takes about 8–10 minutes, or 5–6 without extra angles.

## Versions

| Version | What changes | GPU time measured (Jon) |
| --- | --- | --- |
| A | Today's onboarding: selfie reference 0.35 MP, head-swap LoRA 0.65, 24 steps | ~55 s |
| B | Full-detail selfie reference (1 MP), 40 steps | ~125 s |
| C | B + close-up face pass: the head is cropped, enlarged to 1024 px, redrawn from the selfie and blended back | ~240 s total |
| D | C + local HyperSwap identity polish on the close-up (same GPU run as C) | same as C |
| E | Only with extra angle photos: C using every uploaded angle as a reference | ~275 s |

**Angles mode** compares only A with F: A's exact settings and seed plus the two extra angles, so the extra photos are the only difference. It takes about 2½ minutes. On the owner's photos: A 54 s, F 84 s of onboarding GPU time. A finished session opens directly at `http://127.0.0.1:8765/?session=<id>` while the lab keeps running.

Extra angles must show the person's current look. In the first Jon run, older extra photos with a full beard gave version E a beard his selfie didn't have.

The head-swap LoRA stays at 0.65 in every version. Its authors suggest starting at 1.0, but at 1.0 it gave a short-haired test person long hair. An isolation run confirmed the strength setting, not the reference size or steps, caused it.

## Files

- `lab.py` — local server (stdlib HTTP on 127.0.0.1 only) and the variant pipeline. It reuses the worker's parser, masks, pose lock and CPU compositor, so a winning version drops straight into onboarding.
- `index.html` — the page.
- `../build_face_pass_workflow.py` → `../Qwen21_Face_Pass_1024.api.json` — close-up pass graph, with a redrawn output and a HyperSwap-polished output.
- `services/worker/compose.py` — `face_box` and `blend_face` choose the close-up crop and blend it back, fading out before the crop border.

Everything uploaded or generated stays in the ignored `comfy-identity/likeness-lab-sessions/<session>/`: `selfie.jpg`, crops, versions, face close-ups and `session.json` with the reveal and your pick. Every pick is appended to `likeness-lab-sessions/picks.jsonl`, the running tally for choosing onboarding's default.

## How to use the results

1. Try your own selfie. If none of the versions looks like you, tick what's off and add a note, then share it so the relevant part gets adjusted.
2. When one version consistently looks like you, repeat with people you know well.
3. The version that wins across people becomes onboarding's default. Its onboarding time is shown after each pick, so it can be weighed against the ~90-second target.
