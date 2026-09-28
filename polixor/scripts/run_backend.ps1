<#
    הפעלת שרת Polixor (צד שרת + ממשק בנוי).
    הרצה:  powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
    דגלים: -Port 8756  -Reload  -DataDir "D:\Polixor"
#>
param(
    [int]$Port = 8756,
    [switch]$Reload,
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "Polixor is not installed yet." -ForegroundColor Red
    Write-Host "Double-click Polixor.bat in the main folder to install it."
    exit 1
}

$env:POLIXOR_PORT = $Port
if ($Reload)  { $env:POLIXOR_RELOAD = "1" }
if ($DataDir) { $env:POLIXOR_DATA_DIR = $DataDir }

Push-Location (Join-Path $root "backend")
try {
    # הודעות הקונסולה באנגלית: קונסולת Windows מציגה עברית הפוכה
    Write-Host "`n  Polixor is running: http://127.0.0.1:$Port" -ForegroundColor Green
    Write-Host "  To stop: Ctrl+C`n" -ForegroundColor Gray
    & $venvPy -m polixor.main
} finally {
    Pop-Location
}
