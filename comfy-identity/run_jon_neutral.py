"""Run the verified Jon workflow with the base image's neutral expression."""
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from compare_jon_checkpoints import request, BASE

root = Path(__file__).resolve().parent
workflow = json.loads((root / "Qwen21_Jon_Identity.json").read_text(encoding="utf-8-sig"))
api = json.loads((root / "Qwen21_Jon_Identity.api.json").read_text(encoding="utf-8-sig"))
queue = request("/queue")
if queue["queue_running"] or queue["queue_pending"]:
    raise RuntimeError("ComfyUI has queued work; wait before submitting this comparison.")

prompt = """head_swap: Edit <image1> to show j0n_person, the person from <image2>, while preserving the neutral facial expression of <image1>.
Expression comes from <image1>: a calm, relaxed, neutral face, lips gently closed, mouth corners at rest, no visible teeth, relaxed cheeks, naturally relaxed eyelids and eyebrows. Match the mouth closure, gaze, eyebrow position and cheek tension of <image1>. The final face is neutral and closed-mouth, not smiling or grinning. Do not transfer the broad smile, raised cheeks, wide eyes or expression from <image2>.
Use <image2> only for j0n_person's recognizable identity: facial proportions and bone structure, eye shape, nose, lips, hair, beard, age and natural skin tone. Reconstruct his identity with the neutral expression, original head rotation, tilt, gaze direction, placement, natural scale and neck connection of <image1>.
Preserve the body shape, pose, clothing, garment edges and folds, accessories, camera angle, framing, lighting and background from <image1>. Harmonize every exposed skin region, including face, ears, neck, shoulders, chest, arms and hands, to the same person's natural skin tone under the original lighting. Preserve realistic skin texture and shading; do not copy the reference photo's color cast, clothing, background or pose.
The finished portrait retains <image1>'s quiet neutral expression with closed lips and no teeth, together with j0n_person's identity. Sharp facial detail and a natural hairline and jaw-to-neck transition."""

nodes = {str(n["id"]): n for n in workflow["nodes"]}
api["8"]["inputs"]["prompt"] = prompt
nodes["8"]["widgets_values"][0] = prompt
nodes["8"]["widgets_values_named"]["prompt"] = prompt
nodes["8"]["title"] = "3 - Keep image 1 neutral expression; use Jon identity"
api["8"]["_meta"]["title"] = nodes["8"]["title"]
prefix = "Qwen21_Jon/identity_neutral"
api["11"]["inputs"]["filename_prefix"] = prefix
nodes["11"]["widgets_values"][0] = prefix
nodes["11"]["widgets_values_named"]["filename_prefix"] = prefix
note = """# Jon identity with image 1's expression

Image 1 controls the expression, body, pose, clothing and scene.
Image 2 supplies Jon's identity, hair, beard and skin tone.

This version explicitly requests the calm, closed-mouth expression of image 1.
BFS strength 1.0; Jon identity strength 0.8; 40 steps; fixed seed 42.
Adjust Jon's strength independently in the second LoRA loader.
Keep `head_swap:` and `j0n_person` in the prompt."""
nodes["13"]["widgets_values"] = [note]
nodes["13"]["widgets_values_named"] = {"text": note}
workflow["id"] = str(uuid.uuid4())
for filename, value in (("Qwen21_Jon_Neutral.json", workflow), ("Qwen21_Jon_Neutral.api.json", api)):
    (root / filename).write_text(json.dumps(value, indent=2), encoding="utf-8")

started = time.monotonic()
submission = request("/prompt", {"prompt": api, "client_id": "codex-jon-neutral", "extra_data": {"extra_pnginfo": {"workflow": workflow}}})
prompt_id = submission["prompt_id"]
(root / "jon-neutral-submission.json").write_text(json.dumps(submission, indent=2), encoding="utf-8")
print(f"Queued neutral expression: {prompt_id}", flush=True)
while True:
    history = request("/history/" + prompt_id)
    if prompt_id in history:
        break
    if time.monotonic() - started > 900:
        raise TimeoutError("Inspect the queued job before retrying.")
    time.sleep(5)
result = history[prompt_id]
(root / "jon-neutral-history.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
if result["status"]["status_str"] != "success":
    raise RuntimeError(result["status"])
item = result["outputs"]["11"]["images"][0]
with urllib.request.urlopen(BASE + "/view?" + urllib.parse.urlencode(item), timeout=60) as response:
    (root / "Jon_identity_neutral.png").write_bytes(response.read())
request("/userdata/" + urllib.parse.quote("workflows/Qwen21_Jon_Neutral.json", safe="") + "?overwrite=false", workflow)
print(f"Saved neutral workflow and result in {time.monotonic() - started:.2f}s", flush=True)
