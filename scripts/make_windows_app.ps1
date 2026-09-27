# Put a NEXUS icon on this PC: a desktop shortcut and a Start menu entry. Safe to run again
# (for example after moving the nexus folder).
#   powershell -ExecutionPolicy Bypass -File scripts\make_windows_app.ps1
#
# Clicking the icon starts NEXUS in the background (if it isn't running) and opens it in the browser.
# Clicking it while NEXUS is running asks whether to open it or stop it.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$dir = (Get-Location).Path

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "NEXUS isn't set up yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1" -ForegroundColor Yellow
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
foreach ($folder in $folders) {
    $lnk = $shell.CreateShortcut((Join-Path $folder "NEXUS.lnk"))
    $lnk.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\scripts\nexus_launcher.ps1`""
    $lnk.WorkingDirectory = $dir
    $lnk.IconLocation = "$dir\static\icons\nexus.ico,0"
    $lnk.WindowStyle = 7  # minimised, so no window flashes up
    $lnk.Description = "Start or open NEXUS"
    $lnk.Save()
}

# If NEXUS starts with Windows (phone setup), start it the same way: quietly, with the tray icon.
$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "NEXUS.lnk"
if (Test-Path $startupLnk) {
    $lnk = $shell.CreateShortcut($startupLnk)
    $lnk.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\scripts\nexus_launcher.ps1`" -NoBrowser"
    $lnk.WorkingDirectory = $dir
    $lnk.IconLocation = "$dir\static\icons\nexus.ico,0"
    $lnk.WindowStyle = 7
    $lnk.Save()
}

Write-Host ""
Write-Host "==> NEXUS icon added to your desktop and Start menu." -ForegroundColor Cyan
Write-Host "    Double-click it to start NEXUS (it opens in your browser after a few seconds)."
Write-Host "    While it runs, NEXUS has an icon in the system tray (bottom right): right-click it to Open or Quit."
Write-Host "    Double-click the desktop icon again while it's running to open it or stop it."
Write-Host "    Taskbar: open Start, right-click NEXUS, and choose Pin to taskbar."
