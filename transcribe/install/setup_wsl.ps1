param(
    [ValidateSet("cpu", "gpu")][string]$Mode,
    [string]$Distribution = "Ubuntu-24.04",
    [ValidateSet("both", "asr", "diarization")][string]$Component = "both",
    [ValidateSet("auto", "cu126", "cu128", "cu129")][string]$Cuda = "auto",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "Install WSL2 and Ubuntu first: wsl --install -d Ubuntu-24.04. See README.md."
}
$linuxPath = & wsl.exe -d $Distribution -- wslpath -a $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "Cannot access this checkout in WSL distribution $Distribution." }
$linuxPath = ($linuxPath -join "").Trim()
$setupArgs = @("-d", $Distribution, "--", "bash", "$linuxPath/setup_$Mode.sh", "--component", $Component)
if ($Mode -eq "gpu") { $setupArgs += @("--cuda", $Cuda) }
if ($DryRun) { $setupArgs += "--dry-run" }
& wsl.exe @setupArgs
if ($LASTEXITCODE -ne 0) { throw "WSL setup failed (exit $LASTEXITCODE). Review the output above." }
