@echo off
title StreamHub - Movie Streamer
color 0A
cls
echo.
echo  ====================================================
echo         StreamHub - Direct Movie Streamer
echo  ====================================================
echo.
echo  Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python nahi mila!
    echo.
    echo  Python install karein: https://www.python.org/downloads/
    echo  Install karte waqt "Add Python to PATH" checkbox zaroor tick karein.
    echo.
    pause
    exit /b 1
)
echo  Python mila! Server start ho raha hai...
echo.
echo  Browser automatically open hoga...
echo  Manually bhi open kar sakte ho: http://localhost:5050
echo.
echo  ====================================================
echo  Band karne ke liye: Ctrl+C dabao ya window close karo
echo  Ya stop_server.bat chalao
echo  ====================================================
echo.
python app.py
echo.
echo  Server band ho gaya.
pause
