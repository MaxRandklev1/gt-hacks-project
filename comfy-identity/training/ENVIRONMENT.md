# Isolated Qwen Image 2.1 training environment

Training uses a separate `.venv` in this directory. It does not install packages
into ComfyUI. The development machine's virtual environment uses the existing CPython 3.13.12 standard-library
runtime from the Comfy Desktop standalone environment, but has independent
site-packages (`include-system-site-packages = false`). Keep that base Python
runtime installed while using this environment.

The package source list is [requirements.in](requirements.in). The committed
[requirements.verified.txt](requirements.verified.txt) preserves all 63 package
version pins from the successfully verified environment. It omits the
machine-specific Diffusers `file:///` entry; that library is supplied separately
through the pinned source described below and `requirements.in`. The original
`requirements.freeze.txt` remains a local, ignored installation record.
PyTorch and torchvision use the official CUDA 13.0 wheel index:
<https://download.pytorch.org/whl/cu130>.

For this installation, the exact working `torch 2.12.1+cu130` and
`torchvision 0.27.1+cu130` package files were independently copied from ComfyUI
to avoid a duplicate multi-gigabyte network download. Their dependencies were
installed separately here. This is a file copy, not shared site-packages or a
link to ComfyUI's packages. Torch's separate console-script launchers were not
copied; the training setup invokes Python modules directly.

Diffusers is pinned to commit
`e0abab83b5df05de9e7abd788643c1a7c1e42e28`, matching the reviewed Qwen Image 2.1
training implementation. The script's upstream and local adaptation are kept
under `trainer/` by the training setup.
The pinned library source must be prepared in `diffusers-source/`; it is ignored
by Git. Keeping this short source path avoids the Windows path-length failure
encountered when building from uv's nested cache. On a new machine, install
PyTorch/torchvision from the CUDA wheel index first, then install both
`requirements.verified.txt` and `requirements.in` from this directory. A fresh
environment needs its own Python interpreter; copying packages from ComfyUI is
not a required setup step. See the [reproduction guide](../../docs/REPRODUCIBILITY.md)
for the exact source checkout and example commands.

## Checks

Run from this directory in PowerShell:

```powershell
& .\.venv\Scripts\python.exe .\check_environment.py --output .\environment-health.json
```

This imports the required classes and enumerates CUDA; it does not load model
weights. Once GPU memory is available, an optional small diagnostic checks NF4
forward/backward and two 8-bit Adam updates of small adapter matrices:

```powershell
& .\.venv\Scripts\python.exe .\check_environment.py --gpu-smoke --output .\environment-gpu-health.json
```

Passing the small diagnostic verifies the package/kernel combination. It does
not establish that the full model and training activations fit in 16 GB VRAM or
that a training run has completed.

The local `.uv-cache` contains installation packages only. Model weights are
managed separately. No diagnostic uploads images, model weights, or telemetry.
