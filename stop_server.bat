@echo off
title StreamHub - Stop Server
color 0C
setlocal EnableExtensions EnableDelayedExpansion
set "PORT=5050"
set "FOUND=0"

echo.
echo  ====================================================
echo         StreamHub - Server Stop Kar Raha Hai
echo  ====================================================
echo.

for /f "tokens=5" %%P in ('%SystemRoot%\System32\netstat.exe -ano ^| %SystemRoot%\System32\findstr.exe /R /C:":%PORT% .*LISTENING"') do (
    set "FOUND=1"
    echo  Port %PORT% par PID %%P chal raha hai — band kar raha hun...
    %SystemRoot%\System32\taskkill.exe /PID %%P /T /F >nul 2>&1
    echo  Done! Server band ho gaya.
)

if "!FOUND!"=="0" (
    echo  Port %PORT% par koi server nahi chal raha tha.
)

echo.
%SystemRoot%\System32\timeout.exe /t 2 /nobreak >nul
endlocal
