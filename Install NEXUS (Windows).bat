@echo off
rem Double-click to install NEXUS on this PC: installs what is missing (Python, Ollama), sets NEXUS up,
rem puts the NEXUS icon on your desktop and starts it. Safe to run again (it also repairs a broken setup).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install_windows.ps1"
pause
