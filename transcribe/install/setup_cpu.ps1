param(
    [string]$Distribution = "Ubuntu-24.04",
    [ValidateSet("both", "asr", "diarization")][string]$Component = "both",
    [string]$PythonExecutable = $env:SETUP_PYTHON,
    [switch]$UseWSL,
    [switch]$DryRun
)
if ($UseWSL) {
    & (Join-Path $PSScriptRoot "setup_wsl.ps1") -Mode cpu -Distribution $Distribution -Component $Component -DryRun:$DryRun
} else {
    & (Join-Path $PSScriptRoot "setup_native.ps1") -Mode cpu -Component $Component -PythonExecutable $PythonExecutable -DryRun:$DryRun
}
