# NEXUS launcher for Windows: what the NEXUS desktop / Start menu icon runs.
# Starts NEXUS in the background if it isn't running, then opens it in the browser.
# If NEXUS is already running, asks whether to open it or stop it.
# Create the icon with:  powershell -ExecutionPolicy Bypass -File scripts\make_windows_app.ps1
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $PSScriptRoot
Set-Location $dir
Add-Type -AssemblyName System.Windows.Forms

function Show-Message($text, $icon = "Information", $buttons = "OK") {
    return [System.Windows.Forms.MessageBox]::Show($text, "NEXUS", $buttons, $icon)
}

$py = Join-Path $dir ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Show-Message "NEXUS isn't set up yet.`n`nIn PowerShell, from the nexus folder, run:`npowershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1" "Warning" | Out-Null
    exit 1
}

$port = 8000
if (Test-Path ".env") {
    $line = Select-String -Path ".env" -Pattern '^NEXUS_PORT=\s*"?(\d+)' | Select-Object -Last 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}
$url = "http://localhost:$port"
$dataDir = Join-Path $dir "data"
$pidFile = Join-Path $dataDir "nexus.pid"
$log = Join-Path $dataDir "nexus.log"

function Test-Running {
    try {
        $req = [System.Net.WebRequest]::Create("$url/api/ping")
        $req.Timeout = 2000
        $req.Proxy = $null
        $req.GetResponse().Close()
        return $true
    } catch { return $false }
}

# The NEXUS process this launcher started and that is still loading (so a second click doesn't start another).
function Get-StartingProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    try {
        $p = Get-Process -Id ([int](Get-Content $pidFile -TotalCount 1)) -ErrorAction Stop
        $written = (Get-Item $pidFile).LastWriteTime
        if ($p.ProcessName -like "python*" -and [math]::Abs(($p.StartTime - $written).TotalSeconds) -lt 30) { return $p }
    } catch { }
    return $null
}

if (Test-Running) {
    $answer = Show-Message "NEXUS is already running.`n`nYes  =  open NEXUS`nNo  =  stop NEXUS" "Question" "YesNoCancel"
    if ($answer -eq "Yes") {
        Start-Process $url
    } elseif ($answer -eq "No") {
        $ids = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($id in $ids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
        Show-Message "NEXUS stopped." | Out-Null
    }
    exit 0
}

if (-not (Get-StartingProcess)) {
    # Ollama (the local AI) normally starts with Windows; start it if it isn't running. Captures work without it.
    if (-not (Get-Process -Name "ollama*" -ErrorAction SilentlyContinue)) {
        $ollamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
        if (Test-Path $ollamaApp) { Start-Process $ollamaApp }
    }
    New-Item -ItemType Directory -Force $dataDir | Out-Null
    $proc = Start-Process -FilePath $py -ArgumentList "run.py" -WorkingDirectory $dir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $dataDir "nexus-output.log") -RedirectStandardError $log
    Set-Content -Path $pidFile -Value $proc.Id
}

# Wait for it to come up (the first start after a reboot can take a little while).
for ($i = 0; $i -lt 90; $i++) {
    if (Test-Running) { Start-Process $url; exit 0 }
    if (-not (Get-StartingProcess)) { break }  # it stopped: something went wrong
    Start-Sleep -Seconds 1
}
Remove-Item $pidFile -ErrorAction SilentlyContinue
Show-Message "NEXUS didn't start.`n`nDetails are in:`n$log" "Error" | Out-Null
exit 1
