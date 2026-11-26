param(
    [ValidateSet("cpu", "gpu")][string]$Mode,
    [ValidateSet("both", "asr", "diarization")][string]$Component = "both",
    [ValidateSet("auto", "cu126", "cu128", "cu129")][string]$Cuda = "auto",
    [string]$PythonExecutable = $env:SETUP_PYTHON,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Use setup_cpu.sh or setup_gpu.sh on Linux/macOS." }
if (-not $PythonExecutable) {
    if (Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue) {
        foreach ($version in @("3.12", "3.11", "3.10")) {
            try { $candidate = & py.exe "-$version" -c "import sys; print(sys.executable)" 2>$null }
            catch { continue }
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                $PythonExecutable = ($candidate -join "").Trim()
                break
            }
        }
    }
    if (-not $PythonExecutable) {
        foreach ($name in @("python3.12.exe", "python.exe", "python3.exe")) {
            $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
            if ($command) {
                & $command.Source -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)"
                if ($LASTEXITCODE -eq 0) { $PythonExecutable = $command.Source; break }
            }
        }
    }
}
if (-not $PythonExecutable) { throw "Install Python 3.12 (64-bit), or pass -PythonExecutable with a Python 3.10-3.12 path." }
$setupArgs = @((Join-Path $PSScriptRoot "setup_runtime.py"), "--mode", $Mode, "--component", $Component)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
if ($Mode -eq "gpu") { $setupArgs += @("--cuda", $Cuda) }
if ($DryRun) { $setupArgs += "--dry-run" }
& $PythonExecutable @setupArgs
if ($LASTEXITCODE -ne 0) { throw "Native Windows setup failed (exit $LASTEXITCODE). Review the output above." }
