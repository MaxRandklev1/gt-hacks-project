param(
    [string]$CredentialPath = (Join-Path $env:USERPROFILE '.config\gt-hacks\worker-service-account.json'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $projectRoot 'services\worker\.venv\Scripts\python.exe'
$workerPath = Join-Path $projectRoot 'services\worker\worker.py'
$profilePath = Join-Path $projectRoot 'comfy-identity\training\profiles'
$identityConfig = Join-Path $projectRoot 'comfy-identity\universal_identity\config.json'
$localState = Join-Path $projectRoot '.local'
$projectId = (Get-Content -LiteralPath (Join-Path $projectRoot '.firebaserc') -Raw | ConvertFrom-Json).projects.default
if (!(Test-Path -LiteralPath $CredentialPath)) { throw 'Worker credential is missing. Complete the approved worker identity setup first.' }
if (!(Test-Path -LiteralPath $runtimePath)) { throw 'Install the isolated worker environment described in services/worker/README.md.' }
$env:GOOGLE_APPLICATION_CREDENTIALS = (Resolve-Path -LiteralPath $CredentialPath).Path
$env:FIREBASE_PROJECT_ID = $projectId
$env:FIREBASE_STORAGE_BUCKET = "$projectId.firebasestorage.app"
& $runtimePath $workerPath --check --profiles-root $profilePath --identity-config $identityConfig
if ($LASTEXITCODE -ne 0) { throw 'Worker readiness check failed.' }
if ($CheckOnly) { return }
New-Item -ItemType Directory -Path $localState -Force | Out-Null
$pidFile = Join-Path $localState 'worker-process.json'
if (Test-Path -LiteralPath $pidFile) {
    $previous = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $previous.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks -eq ([datetime]$previous.startTime).ToUniversalTime().Ticks) {
        Write-Output "Worker is already running (PID $($existing.Id))."
        return
    }
}
$arguments = @(('"' + $workerPath + '"'), '--profiles-root', ('"' + $profilePath + '"'))
$process = Start-Process -FilePath $runtimePath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localState 'worker-stdout.log') -RedirectStandardError (Join-Path $localState 'worker-stderr.log')
@{pid=$process.Id;startTime=$process.StartTime.ToUniversalTime().ToString('o');project=$projectId} | ConvertTo-Json | Set-Content -LiteralPath $pidFile
Write-Output "Worker started (PID $($process.Id)). Logs: .local/worker-stderr.log"
