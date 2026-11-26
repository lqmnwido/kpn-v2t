param(
    [string]$Distribution = "Ubuntu-24.04",
    [ValidateSet("both", "asr", "diarization")][string]$Component = "both",
    [ValidateSet("auto", "cu126", "cu128", "cu129")][string]$Cuda = "auto",
    [string]$PythonExecutable = $env:SETUP_PYTHON,
    [switch]$UseWSL,
    [switch]$DryRun
)
if ($UseWSL) {
    & (Join-Path $PSScriptRoot "setup_wsl.ps1") -Mode gpu -Distribution $Distribution -Component $Component -Cuda $Cuda -DryRun:$DryRun
} else {
    & (Join-Path $PSScriptRoot "setup_native.ps1") -Mode gpu -Component $Component -Cuda $Cuda -PythonExecutable $PythonExecutable -DryRun:$DryRun
}
