@echo off
cd /d "%~dp0"
echo ====================================================
echo   Building StreamHub Standalone .exe + version.json
echo ====================================================
python build_exe.py
if errorlevel 1 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)
echo.
echo [DONE] Ready: StreamHub.exe and dist\version.json
pause
