# Build the downloadable NEXUS app for Windows: a folder with NEXUS.exe that runs without Python,
# zipped as dist\NEXUS-<version>-windows.zip. Run it on your PC from the nexus folder:
#   powershell -ExecutionPolicy Bypass -File scripts\build_app_windows.ps1
# People who download it: unzip, double-click NEXUS.exe. Their ideas live in %LOCALAPPDATA%\NEXUS.
# (They still need Ollama from https://ollama.com for the AI; NEXUS's first-run check says so.)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Set NEXUS up first:  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1" -ForegroundColor Yellow
    exit 1
}
$py = ".venv\Scripts\python.exe"
Write-Host "`n==> Installing the build tool (PyInstaller)..." -ForegroundColor Cyan
& $py -m pip install --quiet pyinstaller pystray
Write-Host "`n==> Building (a few minutes)..." -ForegroundColor Cyan
& $py -m PyInstaller packaging\nexus.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Write-Host "The build stopped (see above)." -ForegroundColor Yellow; exit 1 }
$version = (Select-String -Path "app\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$zip = "dist\NEXUS-$version-windows.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\NEXUS" -DestinationPath $zip
# A normal installer too, if Inno Setup is installed (winget install JRSoftware.InnoSetup).
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    Write-Host "`n==> Making the installer..." -ForegroundColor Cyan
    & $iscc "/DAppVersion=$version" packaging\nexus.iss
    if ($LASTEXITCODE -eq 0) { Write-Host "    dist\NEXUS-Setup-$version.exe" }
} else {
    Write-Host "`n(For a NEXUS-Setup.exe installer as well: winget install JRSoftware.InnoSetup, then run this again.)"
}
Write-Host "`n==> Done: $zip" -ForegroundColor Cyan
Write-Host "    Try it: dist\NEXUS\NEXUS.exe (quit your usual NEXUS first, or it just opens that one)."
