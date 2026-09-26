# GT Hacks project collaboration

The user explicitly requested that project changes be committed and pushed to GitHub after each chat turn that changes this project. Preserve this preference across chats working in this checkout.

- Repository: `MaxRandklev1/gt-hacks-project`, intended private visibility.
- At the end of an implementation turn, review the diff, run relevant checks, commit the completed project changes with a clear message, and push to the configured remote. If nothing changed, do not create an empty commit.
- This is authorization to push ordinary project changes; do not ask for repeated push approval. If authentication or remote permissions block publication, keep the local commit and report the precise blocker. Never claim a failed or blocked push succeeded.
- Preserve others' changes. Do not force-push, rewrite shared history, or commit secrets.
- Respect `.gitignore`: original identity photos, local profile libraries and personal model weights, model downloads, environments, logs, machine configuration, and bulk outputs stay local. Selected metadata-stripped demo outputs under `docs/demo/` are intended to be versioned.
- Keep reusable ComfyUI workflows, API graphs, builder scripts, custom-node sources, and setup documentation together. Installed ComfyUI copies are outside this repository; changes there should also be reflected in the authored source here when appropriate.
- Keep prompts person-neutral. The base pose/body, selected identity, and garment reference have separate roles. Preserve the original 1024 output alongside the 4K upscale.
