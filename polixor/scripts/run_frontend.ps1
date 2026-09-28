<#
    שרת פיתוח של הממשק (Vite) עם טעינה מחדש אוטומטית.
    דורש ששרת ה-Python יפעל במקביל (scripts\run_backend.ps1).
    הרצה:  powershell -ExecutionPolicy Bypass -File scripts\run_frontend.ps1
    הודעות הקונסולה באנגלית: קונסולת Windows מציגה עברית הפוכה.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $root "frontend")
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "Installing npm dependencies..." -ForegroundColor Cyan
        npm install
    }
    Write-Host "`n  Dev interface: http://localhost:5173" -ForegroundColor Green
    Write-Host "  (API calls are forwarded to http://127.0.0.1:8756)`n" -ForegroundColor Gray
    npm run dev
} finally {
    Pop-Location
}
