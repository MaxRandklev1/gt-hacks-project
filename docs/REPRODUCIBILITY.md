# Reproducing the local project

The repository versions the source, workflow graphs, their builders, and selected demo exports. Models, personal profiles, source photos, and machine-specific files must be provided separately.

## Local-only configuration

Copy `comfy-identity/universal_identity/config.example.json` to `config.json` and set both absolute paths for this checkout. `profiles_root` must be inside `training_root`. Copy that configured directory into ComfyUI's `custom_nodes/universal_identity` as described in the root README. Do not commit the resulting `config.json`. The existing development machine uses the equivalent folder name `ComfyUI-UniversalIdentityLocal`; keep that installation and do not install a second copy under another name.

The checked-in workflows preserve the tested local example's profile and image filenames. On another machine, upload the pose and garment and choose or create a local person in the profile panel before Run. The original person's photo library and trained adapter are not in Git.

## Builder dependencies

Most builders consume the checked-in workflow from the previous stage. The original historical builders additionally use local inputs:

- `build_workflow.py` reads `object_info.json`. Fetch the current ComfyUI schema first, for example `Invoke-WebRequest http://127.0.0.1:8188/object_info -OutFile comfy-identity/object_info.json`.
- `build_universal_workflow.py` reads `universal-default-profile.json`. Copy the example file and fill in a valid local profile ID; the profile panel/API provides the ID.
- Evaluation scripts refer to prior local run histories and source images. These records remain local; they are not required to import and run the final workflows.

The final portable workflow entry point is `comfy-identity/Qwen21_Universal_TryOn_4K.json`; use the matching `.api.json` for API execution. Builders are optional when importing these exports.

## Isolated training environment

Training uses a separate Python environment in `comfy-identity/training/.venv`; do not install its requirements into ComfyUI's environment. The [environment notes](../comfy-identity/training/ENVIRONMENT.md) describe the tested setup. The package source list is [requirements.in](../comfy-identity/training/requirements.in), and [requirements.verified.txt](../comfy-identity/training/requirements.verified.txt) pins the other 63 packages from the verified environment without including the developer's absolute filesystem paths.

The source list expects a local Diffusers checkout named `diffusers-source`. Obtain [upstream Diffusers](https://github.com/huggingface/diffusers) at commit `e0abab83b5df05de9e7abd788643c1a7c1e42e28`. Starting at the repository root:

```powershell
Set-Location .\comfy-identity\training
git clone https://github.com/huggingface/diffusers.git diffusers-source
git -C diffusers-source checkout e0abab83b5df05de9e7abd788643c1a7c1e42e28
```

For a fresh Windows environment with Python 3.13 installed and available through the Python launcher, run these commands from that training directory. If using an existing environment, keep it and skip the environment-creation command.

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install torch==2.12.1+cu130 torchvision==0.27.1+cu130 --index-url https://download.pytorch.org/whl/cu130
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.verified.txt -r .\requirements.in
```

The last command installs the reviewed local Diffusers checkout alongside the pinned package versions. The snapshot records one verified Windows/CUDA environment; it is not a cross-platform lockfile or a replacement for the environment and training diagnostics. Environments created with an installer that omits `pip` need that installer's equivalent commands.

Training also requires the separate Diffusers-format Qwen checkpoint at `comfy-identity/training/models/Qwen-Image-2.1`; the inference GGUF file is not a training checkpoint. The original and adapted training sources and their pinned provenance are under [comfy-identity/training/trainer](../comfy-identity/training/trainer/).

The [root README's asset table](../README.md#inference-assets) links the recorded model sources and identifies the pre-existing base-model provenance gap. Recorded refinement and upscale revisions/hashes are in the [two-pass](../comfy-identity/TWO_PASS_REALISM_README.md) and [4K](../comfy-identity/UPSCALE_4K_README.md) guides. Model weights are external downloads rather than Git binaries.
