param(
    [string]$EnvFile = ".env",
    [string]$PythonExecutable = ""
)
$ErrorActionPreference = "Stop"
$repository = Split-Path $PSScriptRoot -Parent
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $repository "python/venv/Scripts/python.exe" }
if (-not (Test-Path $PythonExecutable)) { throw "Main Python environment missing; run setup first or pass -PythonExecutable." }
$exportData = & $PythonExecutable (Join-Path $PSScriptRoot "environment_exports.py") --env-file $EnvFile --format json
if ($LASTEXITCODE -ne 0) { throw "Cannot load .env; no settings were applied." }
$settings = ($exportData -join "`n") | ConvertFrom-Json
foreach ($setting in $settings.PSObject.Properties) {
    [Environment]::SetEnvironmentVariable($setting.Name, [string]$setting.Value, "Process")
}
Write-Output 'Settings loaded into this terminal. Run: mvn spring-boot:run'
