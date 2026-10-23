@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_visual_asset_library.ps1"
if errorlevel 1 pause
