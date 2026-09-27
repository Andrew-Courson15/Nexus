# Update NEXUS in one step: stop it, get the latest version, set it up, start it again.
# Your ideas and settings (the data folder and .env) are never touched by an update.
#   powershell -ExecutionPolicy Bypass -File scripts\update_windows.ps1
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$dir = (Get-Location).Path

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n$msg" -ForegroundColor Yellow; exit 1 }

if (-not (Test-Path ".git")) {
    Fail "This copy of NEXUS wasn't installed with git, so it can't update itself.
Download the new version, replace everything except the 'data' folder and the '.env' file, then run
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1"
}

$port = 8000
if (Test-Path ".env") {
    $line = Select-String -Path ".env" -Pattern '^NEXUS_PORT=\s*"?(\d+)' | Select-Object -Last 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}

# 1. Stop every running NEXUS (its window, the desktop icon's hidden one, or the one started with
#    Windows). Otherwise the old version keeps running and phones keep getting it.
Say "Stopping NEXUS..."
$stopped = 0
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($id in $listening) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; $stopped++ }
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run.py*" -and ($_.CommandLine -like "*$dir*" -or $_.ExecutablePath -like "$dir*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $stopped++ }
if ($stopped) { Start-Sleep -Seconds 2; Write-Host "Stopped." } else { Write-Host "NEXUS wasn't running." }
Remove-Item "data\nexus.pid" -ErrorAction SilentlyContinue

# 2. Get the latest version.
Say "Downloading the latest version..."
& git pull
if ($LASTEXITCODE -ne 0) {
    Fail "Couldn't download the update (git pull failed). Check the internet connection and run this again.
If it mentions 'local changes', send a screenshot before doing anything else."
}

# 3. Install anything new (safe to run every time; skips what's already there).
Say "Setting up..."
& powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
if ($LASTEXITCODE -ne 0) { Fail "Setup stopped early (see the message above). Fix that, then run this again." }

# 4. Start the new version (in the background, like the desktop icon) and open it.
Say "Starting NEXUS..."
Start-Process -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") `
    -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\scripts\nexus_launcher.ps1`"" `
    -WindowStyle Hidden
$version = (Select-String -Path "app\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Say "NEXUS $version is starting and will open in your browser."
Write-Host "On your phone: open NEXUS while this PC is on. It loads the new version by itself"
Write-Host "(or close it fully and open it again), then saves its copy for when the PC is off."
Write-Host "Check it in the phone's Settings > This device."
