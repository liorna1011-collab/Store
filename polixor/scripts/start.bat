@echo off
REM ====================================================
REM  Polixor - start the server and open the browser
REM  when it is ready.
REM  Messages are in English on purpose: the Windows
REM  console shows Hebrew reversed.
REM ====================================================
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Polixor is not installed yet.
    echo   Double-click Polixor.bat in the main folder - it installs everything.
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting Polixor...
echo   Your browser will open by itself when the server is ready.
echo   Address: http://127.0.0.1:8756
echo   To stop: close this window.
echo.

REM Open the browser only once the server answers. Opening it
REM earlier shows "can't connect" and the tool looks broken.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){try{$null=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8756/api/health;Start-Process 'http://127.0.0.1:8756';break}catch{Start-Sleep -Seconds 1}}"

REM Brings the packages up to date when requirements.txt changed since the
REM install (quick no-op otherwise).
".venv\Scripts\python.exe" scripts\sync_deps.py

cd backend
"..\.venv\Scripts\python.exe" -m polixor.main

endlocal
pause
