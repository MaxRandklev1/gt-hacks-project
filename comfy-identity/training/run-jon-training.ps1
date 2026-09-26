<#
.SYNOPSIS
Train Jon's Qwen Image 2.1 identity LoRA using the tested local NF4 configuration.
.DESCRIPTION
Uses the existing isolated environment, local model, and prepared dataset next
to this script. Defaults to 400 updates and checkpoints every 100 updates.
Refuses nonempty output directories. Does not install, download, upload, resume,
or delete anything. Offline environment flags are application settings, not a
network firewall. See trainer/README.md for measured health-check results.
.PARAMETER OutputDir
Fresh output directory. Relative paths resolve from this script's directory.
.PARAMETER MaxSteps
Number of optimizer updates. The default 400 is a pilot, not an optimality claim.
.EXAMPLE
.\run-jon-training.ps1 -OutputDir .\outputs\jon-next -MaxSteps 400
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$OutputDir = 'outputs\jon-pilot',

    [ValidateRange(1, 2147483647)]
    [int]$MaxSteps = 400
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$trainingRoot = $PSScriptRoot
$pythonPath = Join-Path $trainingRoot '.venv\Scripts\python.exe'
$trainerPath = Join-Path $trainingRoot 'trainer\train_identity_qwen21.py'
$quantizationPath = Join-Path $trainingRoot 'trainer\nf4.json'
$modelPath = Join-Path $trainingRoot 'models\Qwen-Image-2.1'
$datasetPath = Join-Path $trainingRoot 'dataset'
$outputPath = if ([IO.Path]::IsPathRooted($OutputDir)) {
    [IO.Path]::GetFullPath($OutputDir)
} else {
    [IO.Path]::GetFullPath((Join-Path $trainingRoot $OutputDir))
}

# Guard before launching Python or writing anything into a prior run.
if (Test-Path -LiteralPath $outputPath) {
    if (-not (Test-Path -LiteralPath $outputPath -PathType Container)) {
        throw "Output path is not a directory: $outputPath"
    }
    if (Get-ChildItem -LiteralPath $outputPath -Force | Select-Object -First 1) {
        throw "Refusing to overwrite a nonempty output directory: $outputPath. Choose a fresh -OutputDir."
    }
}

foreach ($requiredFile in @(
    $pythonPath,
    $trainerPath,
    $quantizationPath,
    (Join-Path $modelPath 'model_index.json'),
    (Join-Path $datasetPath 'metadata.jsonl')
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required local file is missing: $requiredFile. This launcher does not download or install files."
    }
}
foreach ($modelComponent in @('transformer', 'text_encoder', 'vae', 'processor', 'scheduler')) {
    if (-not (Test-Path -LiteralPath (Join-Path $modelPath $modelComponent) -PathType Container)) {
        throw "Required local model component is missing: $modelComponent. Complete the local checkpoint before training."
    }
}

$trainerArguments = @(
    $trainerPath,
    '--pretrained_model_name_or_path', $modelPath,
    '--dataset_name', $datasetPath,
    '--image_column', 'image', '--caption_column', 'text',
    '--instance_prompt', 'a photo of j0n_person, a man',
    '--output_dir', $outputPath,
    '--mixed_precision', 'bf16',
    '--bnb_quantization_config_path', $quantizationPath,
    '--text_encoder_4bit',
    '--offload', '--cache_latents', '--gradient_checkpointing', '--use_8bit_adam',
    '--train_batch_size', '1', '--gradient_accumulation_steps', '1',
    '--resolution', '512', '--use_aspect_ratio_buckets',
    '--aspect_ratio_buckets', '512,384;512,448',
    '--center_crop', '--rank', '16', '--lora_alpha', '16',
    '--learning_rate', '1e-4', '--lr_scheduler', 'constant', '--lr_warmup_steps', '0',
    '--max_train_steps', $MaxSteps.ToString(), '--checkpointing_steps', '100',
    '--dataloader_num_workers', '0', '--seed', '42',
    '--report_to', 'none', '--skip_final_inference'
)

$offlineSettings = @{
    HF_HUB_OFFLINE = '1'
    TRANSFORMERS_OFFLINE = '1'
    HF_DATASETS_OFFLINE = '1'
    HF_HUB_DISABLE_TELEMETRY = '1'
    DO_NOT_TRACK = '1'
    WANDB_DISABLED = 'true'
    WANDB_MODE = 'disabled'
    PYTHONUTF8 = '1'
    PYTHONUNBUFFERED = '1'
}
$previousSettings = @{}
foreach ($settingName in $offlineSettings.Keys) {
    $previousSettings[$settingName] = [Environment]::GetEnvironmentVariable($settingName, 'Process')
}

if (-not (Test-Path -LiteralPath $outputPath)) {
    New-Item -ItemType Directory -Path $outputPath | Out-Null
}
$logPath = Join-Path $outputPath 'training.log'

try {
    foreach ($settingName in $offlineSettings.Keys) {
        [Environment]::SetEnvironmentVariable($settingName, $offlineSettings[$settingName], 'Process')
    }
    Write-Host "Training $MaxSteps updates using local files. Output: $outputPath"
    Write-Host "Console log: $logPath"
    & $pythonPath @trainerArguments 2>&1 | Tee-Object -FilePath $logPath
    $trainingExitCode = $LASTEXITCODE
    if ($trainingExitCode -ne 0) {
        throw "Training exited with code $trainingExitCode. Inspect $logPath and any training_health.json in the output directory."
    }
} finally {
    foreach ($settingName in $offlineSettings.Keys) {
        [Environment]::SetEnvironmentVariable($settingName, $previousSettings[$settingName], 'Process')
    }
}
