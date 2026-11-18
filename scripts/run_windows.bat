@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" %*
set "GAZESPACE_EXIT=%ERRORLEVEL%"
if not "%GAZESPACE_EXIT%"=="0" (
    echo GazeSpace setup did not complete. Review the error above.
    pause
)
exit /b %GAZESPACE_EXIT%
