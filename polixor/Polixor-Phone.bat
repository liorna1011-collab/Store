@echo off
REM ====================================================
REM  Polixor - phone mode.
REM  Same as Polixor.bat, but other devices on your home
REM  Wi-Fi (for example an iPhone) can open it too.
REM  The addresses to type on the phone are printed below
REM  once the server starts.
REM  There is no password: use only on a private network.
REM  If Windows Firewall asks, allow "Private networks".
REM ====================================================
setlocal
set POLIXOR_HOST=0.0.0.0
call "%~dp0Polixor.bat"
endlocal
