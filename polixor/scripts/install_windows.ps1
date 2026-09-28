<#
    Polixor - התקנה ל-Windows
    ==========================
    מתקין את התלויות: Python, FFmpeg וחבילות Python.
    הממשק מגיע כבר בנוי, ולכן Node.js נדרש רק לבנייה מחדש שלו.

    הדרך הפשוטה: לחיצה כפולה על Polixor.bat בתיקייה הראשית.

    הרצה ידנית (PowerShell):
        powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1

    דגלים:
        -SkipFrontend     דילוג על בניית הממשק
        -RebuildFrontend  בנייה מחדש של הממשק גם אם הוא קיים (דורש Node.js)
        -Cuda             התקנת CTranslate2 עם תמיכת GPU
        -WhisperModel     הורדה מראש של מודל התמלול (tiny/base/small/medium/large-v3)

    הערות קידוד (חשוב):
      • הקובץ שמור ב-UTF-8 **עם BOM**. PowerShell 5.1 — ברירת המחדל בכל
        Windows — קורא קובץ בלי BOM בקידוד ANSI של המערכת, והאותיות
        „ד" ו„ה" הופכות שם לתווי מירכאות שחותכים מחרוזות. בלי BOM
        הסקריפט הזה לא עובר אפילו ניתוח תחבירי.
      • ההודעות שמוצגות בחלון הן באנגלית. קונסולת Windows אינה תומכת
        בכיווניות עברית, ועברית מוצגת בה הפוכה — בדיוק כשצריך לקרוא
        הודעת שגיאה.
#>

param(
    [switch]$SkipFrontend,
    [switch]$RebuildFrontend,
    [switch]$Cuda,
    [string]$WhisperModel = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Write-Step($msg)  { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "  [X]  $msg" -ForegroundColor Red }

function Test-Command($name) {
    $null = Get-Command $name -ErrorAction SilentlyContinue
    return $?
}

function Update-SessionPath {
    # התקנה דרך winget מעדכנת את ה-PATH ברישום, לא בחלון הנוכחי
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-WithWinget($id, $label) {
    if (-not (Test-Command "winget")) {
        Write-Err "winget is not available. Please install $label manually."
        return $false
    }
    Write-Host "  Installing $label (this can take a minute)..."
    # הפלט של winget חייב ללכת ל-Out-Host. בלי זה הוא הופך לחלק מערך
    # ההחזרה של הפונקציה: מערך לא-ריק הוא TRUE ב-PowerShell, ולכן
    # התקנה שנכשלה דווחה כהצלחה. (נבדק ב-PowerShell אמיתי.)
    winget install --id $id -e --source winget --accept-package-agreements `
                   --accept-source-agreements --silent | Out-Host
    return ($LASTEXITCODE -eq 0)
}

Write-Host @"

  ____       _ _
 |  _ \ ___ | (_)_  _____  _ __
 | |_) / _ \| | \ \/ / _ \| '__|
 |  __/ (_) | | |>  < (_) | |
 |_|   \___/|_|_/_/\_\___/|_|

  Windows setup
"@ -ForegroundColor Blue

# --------------------------------------------------------------------------
Write-Step "Checking Python"

function Find-Python {
    # „py" קודם: ב-Windows נקי, „python" הוא לרוב קיצור של חנות
    # Microsoft שלא מריץ כלום ורק מדפיס הודעה.
    foreach ($cmd in @("py", "python")) {
        if (-not (Test-Command $cmd)) { continue }
        $v = (& $cmd --version 2>&1 | Out-String).Trim()
        if ($v -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) { Write-Ok $v; return $cmd }
            Write-Warn2 "$v found, but Python 3.10 or newer is required"
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Warn2 "Python 3.10+ not found."
    if (Install-WithWinget "Python.Python.3.12" "Python 3.12") {
        # במקום לעצור ולבקש הרצה חוזרת: מרעננים את ה-PATH ומחפשים שוב.
        # `exit 0` כאן היה מסמן הצלחה — ומשגר שבודק את קוד היציאה היה
        # ממשיך להפעיל שרת בלי סביבה וירטואלית.
        Update-SessionPath
        $python = Find-Python
    }
    if (-not $python) {
        Write-Err "Python 3.10+ is still not available."
        Write-Err "Install it from https://www.python.org/downloads/"
        Write-Err "and tick 'Add python.exe to PATH' on the first screen, then run again."
        exit 1
    }
}

# --------------------------------------------------------------------------
Write-Step "Checking FFmpeg"
if (Test-Command "ffmpeg") {
    Write-Ok ((ffmpeg -version 2>&1 | Select-Object -First 1))
} else {
    Write-Warn2 "FFmpeg not found."
    $null = Install-WithWinget "Gyan.FFmpeg" "FFmpeg"
    Update-SessionPath
    # בודקים שוב במקום לסמוך על קוד היציאה בלבד — מה שקובע הוא
    # שהפקודה באמת זמינה עכשיו.
    if (Test-Command "ffmpeg") {
        Write-Ok "FFmpeg installed"
    } else {
        Write-Err "FFmpeg is not available. Polixor cannot process video without it."
        Write-Err "Install it from https://www.gyan.dev/ffmpeg/builds/ and add it to PATH,"
        Write-Err "or close this window, reopen it, and run Polixor.bat again."
        exit 1
    }
}

# --------------------------------------------------------------------------
Write-Step "Creating the Python environment"
$venv = Join-Path $root ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $python -m venv $venv
    if (-not (Test-Path $venvPy)) { Write-Err "Could not create the environment"; exit 1 }
    Write-Ok "Created at $venv"
} else {
    Write-Ok "Already exists"
}

# --------------------------------------------------------------------------
Write-Step "Installing Python packages (first time: a few minutes)"
& $venvPy -m pip install --upgrade pip --quiet
# `python -m pip` ולא pip.exe: אחרי שדרוג עצמי, pip.exe עלול להיות
# נעול ב-Windows וההתקנה נכשלת בלי סיבה נראית לעין.
& $venvPy -m pip install -r (Join-Path $root "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Err "Package installation failed. The messages above say which package."
    # מוחקים סביבה חלקית, כדי שההרצה הבאה של Polixor.bat תנסה שוב
    # ולא תניח שהכול מותקן רק משום שהתיקייה קיימת.
    Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
    exit 1
}
Write-Ok "All packages installed"

if ($Cuda) {
    Write-Step "GPU support (CUDA)"
    & $venvPy -m pip install --upgrade "ctranslate2>=4.0"
    Write-Warn2 "cuDNN 9 and cuBLAS must be installed on the system."
    Write-Warn2 "See: https://opennmt.net/CTranslate2/installation.html"
}

# --------------------------------------------------------------------------
if ($WhisperModel) {
    Write-Step "Downloading transcription model: $WhisperModel"
    $dl = @"
from faster_whisper import WhisperModel
import os
root = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'Polixor', 'models')
os.makedirs(root, exist_ok=True)
print('Downloading...')
WhisperModel('$WhisperModel', device='cpu', compute_type='int8', download_root=root)
print('Saved to:', root)
"@
    & $venvPy -c $dl
    if ($LASTEXITCODE -eq 0) { Write-Ok "Model downloaded" }
    else { Write-Warn2 "Model download failed - it will download automatically on first use." }
}

# --------------------------------------------------------------------------
# הממשק מגיע כבר בנוי בתוך החבילה (frontend\dist). במקרה כזה אין
# צורך ב-Node.js בכלל — התקנה שלו רק מוסיפה נקודת כשל.
$builtUi = Join-Path $root "frontend\dist\index.html"
if ((Test-Path $builtUi) -and -not $RebuildFrontend) {
    Write-Step "User interface"
    Write-Ok "Already built and included - Node.js is not needed"
    $SkipFrontend = $true
}

if (-not $SkipFrontend) {
    Write-Step "Building the user interface"
    if (-not (Test-Command "npm")) {
        $null = Install-WithWinget "OpenJS.NodeJS.LTS" "Node.js LTS"
        Update-SessionPath
        if (-not (Test-Command "npm")) {
            Write-Err "Node.js is not available. Install Node.js 18+ from https://nodejs.org"
            exit 1
        }
    }
    Push-Location (Join-Path $root "frontend")
    try {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        Write-Ok "Interface built into frontend\dist"
    } catch {
        Write-Err $_
        exit 1
    } finally {
        Pop-Location
    }
}

# --------------------------------------------------------------------------
Write-Step "Health check"
& $venvPy -c @"
import sys
sys.path.insert(0, r'$root\backend')
from polixor.config import system_report
r = system_report()
ok = lambda b: 'yes' if b else 'MISSING'
print('  Python         :', r['python'])
print('  FFmpeg         :', ok(r['ffmpeg']['available']))
print('  yt-dlp         :', ok(r['modules']['yt_dlp']['available']))
print('  faster-whisper :', ok(r['modules']['faster_whisper']['available']))
print('  OpenCV         :', ok(r['modules']['cv2']['available']))
print('  GPU            :', r['gpu']['name'] or 'not detected')
print('  Data folder    :', r['data_dir'])
"@
if ($LASTEXITCODE -ne 0) {
    Write-Err "The health check failed - see the messages above."
    exit 1
}

Write-Host "`n  Setup complete." -ForegroundColor Green
exit 0
