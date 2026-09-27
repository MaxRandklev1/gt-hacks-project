# Starts the local Likeness Lab at http://127.0.0.1:8765 (Ctrl+C to stop). Requires ComfyUI to be running.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $projectRoot 'services\worker\.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $runtimePath)) { throw 'Install the worker environment described in services/worker/README.md.' }
try { Invoke-RestMethod -Uri 'http://127.0.0.1:8188/system_stats' -TimeoutSec 5 | Out-Null } catch { throw 'Start ComfyUI first.' }
Start-Process 'http://127.0.0.1:8765'
& $runtimePath (Join-Path $projectRoot 'comfy-identity\likeness_lab\lab.py')
