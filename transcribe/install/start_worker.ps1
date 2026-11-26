param(
    [string]$EnvFile = ".env",
    [string]$PythonExecutable = ""
)
$ErrorActionPreference = "Stop"
$repository = Split-Path $PSScriptRoot -Parent
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $repository "python/venv/Scripts/python.exe" }
if (-not (Test-Path $PythonExecutable)) { throw "Main Python environment missing. Run setup_cpu.ps1 or setup_gpu.ps1 first, or pass -PythonExecutable." }
& $PythonExecutable (Join-Path $PSScriptRoot "run_worker.py") --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { throw "Worker stopped with exit code $LASTEXITCODE." }
