# Likeness Lab review

Reviewed September 26, 2026 against [`d96023c`](https://github.com/MaxRandklev1/gt-hacks-project/commit/d96023c). This review inspected the local server, source, both completed Jon sessions, original Comfy outputs/history, and installed swap-node preprocessing. It did not submit a new GPU job, upload a photo, cast a participant vote, or change the live pipeline.

## Verdict

The lab is a useful way to ask people whether a result resembles them, but it is not ready to select a production winner. The purported HyperSwap variant is identical to its unpolished counterpart because the face-swap path silently fails. Voting and session recovery also need repair before collecting results. No participant likeness judgments have been saved in the reviewed sessions.

The local page and its four garment choices work. The server listens on `127.0.0.1:8765`, and its code does not send uploads or picks to Firebase. **73 worker tests, 114 web tests and TypeScript passed.** The existing tests do not exercise the lab's HTTP/session/voting flow or the failing face-swap graph; the added worker test covers crop bounds and blend-border fading.

## Blocking finding: the face-correction comparison is a no-op

The face-pass builder connects node 17's VAE output directly to the swap node's target input. The actual saved output has four channels (RGBA), while the installed face detector's preprocessing allocates a three-channel frame.

Executing that installed preprocessing method against the saved crop reproduced:

```text
could not broadcast input array from shape (640,640,4) into shape (640,640,3)
```

Converting the same image to RGB passed preprocessing. The detector catches its failure and returns no detected faces; the swap returns the original target, while Comfy reports success.

Evidence:

- All four saved raw redrawn/polished output pairs have exactly identical pixels.
- C and D's final full portraits are also pixel-identical in both completed sessions: zero changed pixels and zero maximum difference.
- This duplication occurs before the lab's blending, so the compositor is not responsible for losing the correction.

Fix the target's channel conversion before HyperSwap, then verify successful face detection and a real swap. Reject or flag a failed/unchanged variant instead of presenting it as a distinct method. Preserve the old sessions as invalid evidence for D, rather than silently replacing their images. Duplicate C/D entries currently give the same picture two voting slots.

Relevant source: [face-pass builder](../../comfy-identity/build_face_pass_workflow.py), line 34; [face-pass graph](../../comfy-identity/Qwen21_Face_Pass_1024.api.json), node 25; [lab.py](../../comfy-identity/likeness_lab/lab.py), `face_pass`.

## Vote collection needs one final blind submission

Selecting a candidate immediately submits and reveals the methods. Every subsequent click overwrites the session's pick and appends another entry to `picks.jsonl`, including choices made after the reveal. Counting rows would overcount repeat voters and mix blind and informed judgments.

The feedback form asks what is wrong with the chosen result, but notes entered after the first click are not saved unless the participant votes again. This creates both missing feedback and duplicate votes.

Let participants select a candidate or none, add notes, and then explicitly submit once. Make that submission idempotent on the server. Preserve the first blind choice; record any later corrections separately, without counting them as additional participants. Store whether the result is acceptable, not merely the least-wrong choice.

Relevant source: [lab.py](../../comfy-identity/likeness_lab/lab.py), lines 290–303; [index.html](../../comfy-identity/likeness_lab/index.html), `pick` and the feedback section.

## Refresh and restart lose the comparison

The browser keeps its session ID only in memory. The server also starts with an empty in-memory session table and never reloads saved sessions. A previous completed session returned 404 from the session API despite its saved selfie still being available. Refreshing during a long run or restarting the server leaves the user without a supported way to resume their comparison.

Polling does not check HTTP status or catch network failures. A session 404 is treated as completion and passed to the result renderer without cards.

Persist a resumable session identifier, restore validated sessions from disk, and handle unavailable/failed requests explicitly. Save graph/settings versions, reference hashes, seed and garment key with the session; the current JSON lacks sufficient provenance to replay an old experiment after code changes.

## Interpretation of the model tests

- **A is a valid inference baseline.** Its 23 reachable inference nodes match the production personal-base graph after normalizing output paths. BFS strength is 0.65 in production at `d7a6184`, `4c1c792`, and the reviewed version; the lab did not lower production's strength. The lab nevertheless omits production's initial selfie validator and generated-face/alignment rejection checks, so it does not reproduce the entire onboarding journey.
- **A versus B compares packages.** B changes both reference resolution and step count. A preference for B cannot identify which change helped without another control.
- **C visibly changes texture and facial geometry.** Extra detail is not proof that the new chin, mouth or proportions are more faithful. The person photographed must judge that.
- **D has not been successfully evaluated**, because of the four-channel failure above.
- **E mixes appearances in the saved Jon tests.** Older additional photos have facial hair absent from the primary selfie, and the result grows a fuller beard. Both complete sessions use the same extra crops; these are not independent subject tests.
- **One optional extra is mishandled.** `add_extras` accepts one extra image, but its instruction always references both image3 and image4. Generate instructions for the images actually supplied.
- **The strength experiment has a limited conclusion.** The consultant's controlled neutral-source comparison changes only BFS strength from 0.65 to 1.0, with the same profile, seed, steps and byte-identical base. Long hair appeared at 1.0 in that case. However, this review's earlier smiling-Jon run used 0.65, 24 steps, seed 42, 0.35 MP and the final prompt and still invented shoulder-length hair. Strength 0.65 is not a universal hair-preservation fix.
- The proposed real-photo head-compositing control is still absent. The lab compares generative variants, not every approach in the agreed A/B/C design discussion.

## Crop and blend review

The new blend fades to zero before the crop border. No head pixels were cut off in the four inspected short/long-hair examples. The crop is sized around all parsed hair and head coverings, though, so a 1024-pixel crop is not a 1024-pixel face. Measured parser bounds imply roughly 456 pixels across the short-haired example's face versus 266 pixels for a long-curly-hair example. Universal long-hair behavior and significant new head geometry remain unproven.

Existing whole-image limitations still apply: exposed-skin mismatch, neckline remnants and clothing overlap with large beards. A face-crop improvement alone does not validate these.

## Timing and GPU sharing

Latest saved stage measurements are A 52.6 seconds, B 125.1, C/D 239.6, and E 271.1. The complete session took approximately 9 minutes 40 seconds. These exclude some shared photo preparation and final compositing. The C/D number includes B plus a shared graph containing both the redraw and the attempted swap; it is not C-alone time or an independently measured incremental cost for D. UI text calling these GPU or onboarding times should be corrected.

The lab's `threading.Lock` protects only the lab process. The live Firebase worker remains separate and can submit work to the same Comfy queue. The lab neither reserves that worker's GPU access nor prevents a new live onboarding request during a comparison. Run comparisons while the demo is idle, and introduce coordinated scheduling before simultaneous use. The lab also has a check-then-start race: its request handler checks the lock before the new thread acquires it, so concurrent submissions can both be accepted.

## Recommended next test

Repair RGB conversion, swap failure detection, first-blind-vote capture and session recovery. Then rerun the exact selfie from the owner's failed live test, without tuning to that person. Compare face shape, chin, mouth, age, hair and expression, with an explicit unacceptable/none option. Once an acceptable method exists, repeat on other real people and measure its complete onboarding cost. Do not replace the live identity stage based on the current Jon-only, duplicate-variant results.

Original photos, picks and generated outputs remain local and ignored. This review adds documentation only.
