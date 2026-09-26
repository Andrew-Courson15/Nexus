@echo off
rem Start NEXUS on Windows. Double-click this file. Close the window (or press Ctrl+C) to stop.
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  echo NEXUS isn't set up yet. In PowerShell, from the nexus folder, run:
  echo   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
  pause
  exit /b 1
)
rem Open the browser a few seconds after the server starts.
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:8000"
".venv\Scripts\python.exe" run.py
pause
