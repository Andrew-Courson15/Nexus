# One-step NEXUS install for Windows: installs what's missing (Python, Ollama), sets NEXUS up,
# puts the NEXUS icon on the desktop and starts it. Safe to run again.
# Easiest: double-click "Install NEXUS (Windows).bat" in the nexus folder.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$dir = (Get-Location).Path

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n$msg" -ForegroundColor Yellow; exit 1 }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $ollamaDir = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
    if (Test-Path $ollamaDir) { $env:Path += ";$ollamaDir" }
}
function Get-WithWinget($id, $name) {
    Say "Installing $name (Windows may ask for permission)..."
    & winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

Write-Host "NEXUS installer: this sets up everything NEXUS needs on this PC." -ForegroundColor Cyan
Refresh-Path
if (-not (Has winget)) {
    Fail "This installer uses 'winget', which comes with Windows 10 (2021 and later) and Windows 11.
Install 'App Installer' from the Microsoft Store, then double-click the installer again."
}

# 1. Python 3.10 or newer
$pyOk = $false
foreach ($cand in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
    $parts = $cand.Split(" ")
    if (-not (Has $parts[0])) { continue }
    $ok = & $parts[0] @($parts | Select-Object -Skip 1) -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
    if ("$ok".Trim() -eq "True") { $pyOk = $true; break }
}
if (-not $pyOk) { Get-WithWinget "Python.Python.3.12" "Python 3.12" }

# 2. Ollama (runs the AI on this PC) - installed and running
$ollamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
if (-not (Has ollama) -and -not (Test-Path $ollamaApp)) { Get-WithWinget "Ollama.Ollama" "Ollama (the local AI)" }
$up = $false
try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; $up = $true } catch { }
if (-not $up -and (Test-Path $ollamaApp)) {
    Say "Starting Ollama..."
    Start-Process $ollamaApp
    for ($i = 0; $i -lt 30 -and -not $up; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null; $up = $true } catch { }
    }
}
if (-not $up) {
    Fail "Ollama is installed but isn't running yet. Start 'Ollama' from the Start menu, then double-click the installer again."
}

# 3. First time only: choose the AI model that suits this PC's memory.
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    if ($ramGB -lt 12) {
        (Get-Content ".env") -replace "^NEXUS_OLLAMA_MODEL=.*", "NEXUS_OLLAMA_MODEL=gemma3:4b" | Set-Content ".env"
        Write-Host "This PC has $ramGB GB of memory, so NEXUS will use the lighter gemma3:4b model."
    }
}

# 4. NEXUS itself (Python packages, models, speech model, desktop icon)
& powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
if ($LASTEXITCODE -ne 0) { Fail "Setup stopped early (see the message above). Fix that, then double-click the installer again." }

# 5. Phone (optional, needs Tailscale)
if ((Has tailscale) -or (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")) {
    $ans = Read-Host "`nSet up NEXUS for your phone now? (y/n)"
    if ($ans -match "^[yY]") { & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\phone_setup_windows.ps1 }
} else {
    Write-Host "`nFor your phone later: install Tailscale (https://tailscale.com/download), then open NEXUS > Settings > Your phone."
}

# 6. Start NEXUS
Say "Starting NEXUS..."
Start-Process -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") `
    -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\scripts\nexus_launcher.ps1`"" -WindowStyle Hidden
Say "NEXUS is installed. It opens in your browser in a moment."
Write-Host "Next time, double-click the NEXUS icon on your desktop."
