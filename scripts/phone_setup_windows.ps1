# Connect your phone to NEXUS privately, from anywhere, using Tailscale.
# Safe to run again. Run from the nexus folder:
#   powershell -ExecutionPolicy Bypass -File scripts\phone_setup_windows.ps1
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n$msg" -ForegroundColor Yellow; exit 1 }

# 1. Tailscale installed?
$ts = $null
$cmd = Get-Command tailscale -ErrorAction SilentlyContinue
if ($cmd) { $ts = $cmd.Source }
elseif (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe") { $ts = "$env:ProgramFiles\Tailscale\tailscale.exe" }
if (-not $ts) {
    Fail "Tailscale isn't installed on this PC yet.
Download it from https://tailscale.com/download/windows, install it, sign in
(any Google/Microsoft/Apple account works), then run this script again."
}

# 2. Signed in, and what is this PC called on the private network?
$raw = & $ts status --json 2>$null | Out-String
# Read the two values we need with plain text matching (older PowerShell's JSON reader can
# fail on Tailscale's output). "Self" comes before the other devices, so the first DNSName is ours.
$state = if ($raw -match '"BackendState"\s*:\s*"([^"]*)"') { $Matches[1] } else { "" }
if ($state -ne "Running") {
    Fail "Tailscale is installed but not connected. Click the Tailscale icon near the clock,
choose 'Log in', then run this script again."
}
$name = if ($raw -match '"DNSName"\s*:\s*"([^"]*)"') { $Matches[1].TrimEnd(".") } else { "" }
if (-not $name) {
    Fail "Couldn't find this PC's Tailscale name. Turn on MagicDNS at
https://login.tailscale.com/admin/dns and run this script again."
}
Say "This PC's private address: https://$name"

# 3. Let NEXUS answer to that name (it only answers to names it knows).
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
$lines = Get-Content ".env"
$found = $false
$changed = $false
$lines = foreach ($line in $lines) {
    if ($line -match "^NEXUS_ALLOWED_HOSTS=(.*)$") {
        $found = $true
        $hosts = @($Matches[1].Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($hosts -notcontains $name) { $hosts += $name; $changed = $true }
        "NEXUS_ALLOWED_HOSTS=" + ($hosts -join ",")
    } else { $line }
}
if (-not $found) { $lines += "NEXUS_ALLOWED_HOSTS=localhost,127.0.0.1,$name"; $changed = $true }
if ($changed) { Set-Content -Path ".env" -Value $lines; Say "Added $name to NEXUS_ALLOWED_HOSTS in .env" }

# 4. Private HTTPS link: https://<name> -> NEXUS on this PC (only your devices can reach it).
Say "Turning on the private HTTPS link..."
& $ts serve --bg 8000
if ($LASTEXITCODE -ne 0) {
    Fail "Tailscale couldn't turn on the link.
- If it mentions HTTPS or certificates: open https://login.tailscale.com/admin/dns,
  turn on MagicDNS and 'HTTPS Certificates', then run this script again.
- If it says access denied: open PowerShell as Administrator and run it again."
}

# 5. Optional: start NEXUS when the PC starts, so the phone can always reach it.
$ans = Read-Host "`nStart NEXUS automatically when this PC starts? (y/n)"
if ($ans -match "^[yY]") {
    $startup = [Environment]::GetFolderPath("Startup")
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $startup "NEXUS.lnk"))
    $lnk.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$((Get-Location).Path)\scripts\nexus_launcher.ps1`" -NoBrowser"
    $lnk.IconLocation = "$((Get-Location).Path)\static\icons\nexus.ico,0"
    $lnk.WorkingDirectory = (Get-Location).Path
    $lnk.WindowStyle = 7
    $lnk.Save()
    Say "NEXUS will start in the background (icon in the system tray) when you sign in to Windows."
}

Say "PC is ready. Now:"
Write-Host "  1. Restart NEXUS: close its window, then double-click scripts\start_windows.bat"
Write-Host "  2. On your iPhone: install 'Tailscale' from the App Store and sign in with the SAME account."
Write-Host "  3. On your iPhone, open Safari and go to:  https://$name"
Write-Host "     (the very first visit can take ~20 seconds while the secure certificate is created)"
Write-Host "  4. Tap the Share button, then 'Add to Home Screen'. NEXUS is now an app on your phone."
