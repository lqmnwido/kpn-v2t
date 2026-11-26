# No Pester, Windows host, model downloads or installation required.
$ErrorActionPreference = "Stop"
$installer = Split-Path $PSScriptRoot -Parent
function Assert-Setup($Condition, $Message) { if (-not $Condition) { throw $Message } }
foreach ($file in Get-ChildItem $installer -Filter *.ps1) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors) > $null
    Assert-Setup (-not $parseErrors.Count) "PowerShell parse failed: $($file.Name)"
}
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("worker-ps-tests-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory $temporary > $null
try {
    Copy-Item (Join-Path $installer "setup_cpu.ps1") $temporary
    Copy-Item (Join-Path $installer "setup_gpu.ps1") $temporary
    $stub = @'
param($Mode, $Component, $Cuda="auto", $PythonExecutable, $Distribution, [switch]$DryRun)
[pscustomobject]@{
    Mode=$Mode; Component=$Component; Cuda=$Cuda; Python=$PythonExecutable;
    Distribution=$Distribution; DryRun=[bool]$DryRun; Route="ROUTE"
} | ConvertTo-Json -Compress
'@
    $stub.Replace('ROUTE','native') | Set-Content (Join-Path $temporary "setup_native.ps1")
    $stub.Replace('ROUTE','wsl') | Set-Content (Join-Path $temporary "setup_wsl.ps1")
    $cpu = & (Join-Path $temporary "setup_cpu.ps1") -Component asr -DryRun -PythonExecutable 'C:/Program Files/Python/python.exe' | ConvertFrom-Json
    Assert-Setup ($cpu.Route -eq 'native' -and $cpu.Mode -eq 'cpu' -and $cpu.DryRun) 'CPU must default to native dry-run'
    Assert-Setup ($cpu.Python -eq 'C:/Program Files/Python/python.exe' -and $cpu.Component -eq 'asr') 'CPU arguments changed'
    $gpu = & (Join-Path $temporary "setup_gpu.ps1") -Cuda cu128 -Component diarization -DryRun | ConvertFrom-Json
    Assert-Setup ($gpu.Route -eq 'native' -and $gpu.Mode -eq 'gpu' -and $gpu.Cuda -eq 'cu128' -and $gpu.Component -eq 'diarization') 'GPU arguments changed'
    $wsl = & (Join-Path $temporary "setup_gpu.ps1") -UseWSL -Distribution Ubuntu -DryRun | ConvertFrom-Json
    Assert-Setup ($wsl.Route -eq 'wsl' -and $wsl.Distribution -eq 'Ubuntu' -and $wsl.DryRun) 'Explicit WSL routing failed'
    Write-Output 'PowerShell syntax and native/WSL argument routing checks passed; no installers were run'
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
