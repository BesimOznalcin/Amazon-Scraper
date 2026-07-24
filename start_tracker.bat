@echo off
cd /d "%~dp0"

REM Zaten calisiyorsa ikinci ornek baslatma
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_tracker.ps1"
