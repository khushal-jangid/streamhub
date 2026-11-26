@echo off
title StreamHub VIP Admin Key Generator
cd /d "%~dp0"
python "%~dp0vip_admin.py"
if errorlevel 1 pause
