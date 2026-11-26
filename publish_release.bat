@echo off
cd /d "%~dp0"
set /p VER="Naya version number daalo (e.g. 2.4.1, blank = current APP_VERSION): "
set /p NOTES="Update notes daalo (optional): "
if "%VER%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_release.ps1" -Notes "%NOTES%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_release.ps1" -Version "%VER%" -Notes "%NOTES%"
)
pause
