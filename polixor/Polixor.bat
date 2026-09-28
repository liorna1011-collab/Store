@echo off
REM ====================================================
REM  Polixor - double-click to start.
REM  First run: installs everything. Every run: starts
REM  the server and opens your browser.
REM ====================================================
setlocal
cd /d "%~dp0"
title Polixor

REM Opening a file straight from inside the ZIP runs it alone from a
REM temp folder, without the other files. It is the most common
REM mistake, so it is checked first.
if not exist "%~dp0scripts\install_windows.ps1" (
    echo.
    echo   It looks like this was opened from inside the ZIP.
    echo   Right-click polixor.zip, choose "Extract All",
    echo   then double-click Polixor.bat in the extracted folder.
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo.
    echo   First-time setup - a few minutes, only once.
    echo   If Windows asks for permission to install Python or FFmpeg, allow it.
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
    if errorlevel 1 (
        echo.
        echo   Setup did not finish. The messages above explain what is missing.
        echo   Fix it, then double-click Polixor.bat again.
        echo.
        pause
        exit /b 1
    )
)

call "%~dp0scripts\start.bat"
endlocal
