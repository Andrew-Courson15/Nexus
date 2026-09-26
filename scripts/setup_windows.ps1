# One-time NEXUS setup for Windows. Safe to run again at any time.
# Run from the nexus folder:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`n$msg" -ForegroundColor Yellow; exit 1 }

# 1. Python 3.10 or newer
$py = $null
foreach ($cand in @("py -3.12", "py -3.13", "py -3.11", "py -3.10", "python")) {
    $parts = $cand.Split(" ")
    $exe = $parts[0]
    $rest = @($parts | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $ok = & $exe @rest -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
    if ("$ok".Trim() -eq "True") { $py = $parts; break }
}
if (-not $py) {
    Fail "Python 3.10+ is needed. Install it with:  winget install Python.Python.3.12
(or from https://www.python.org/downloads/ - tick 'Add python.exe to PATH'), close this window, open a new one, and run this script again."
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Say ("Using " + (& $pyExe @pyArgs --version))

# 2. Virtual environment + packages (NEXUS + local Whisper)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment (.venv)." }
}
$venvPy = ".venv\Scripts\python.exe"
Say "Installing Python packages (a few minutes the first time)..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt -r requirements-local.txt
if ($LASTEXITCODE -ne 0) { Fail "Package installation failed. Copy the messages above and send them to Claude." }

# 3. Settings file
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Say "Created .env from .env.example" }
$modelLine = Select-String -Path ".env" -Pattern "^NEXUS_OLLAMA_MODEL=(.*)$" | Select-Object -First 1
$model = if ($modelLine) { $modelLine.Matches[0].Groups[1].Value.Trim() } else { "" }
if (-not $model) { $model = "gemma3:12b" }

# 4. Graphics card check (advice only)
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    $name, $mem = "$gpu".Split(",")
    $memMb = 0
    if ($mem) { [int]::TryParse($mem.Trim(), [ref]$memMb) | Out-Null }
    if ($memMb -gt 0) {
        Say ("Graphics card: " + $name.Trim() + " with " + $memMb + " MB of video memory")
        if ($memMb -lt 11000 -and $model -eq "gemma3:12b") {
            Write-Host "This card has less than 12 GB. gemma3:12b will still work but slower." -ForegroundColor Yellow
            Write-Host "For speed, edit .env: NEXUS_OLLAMA_MODEL=gemma3:4b  and run this script again." -ForegroundColor Yellow
        }
    }
}

# 5. Ollama (the local model runner)
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Fail "Ollama isn't installed yet. Download it from https://ollama.com/download (Windows),
install it, then close this window, open a new one, and run this script again."
}
try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null }
catch { Fail "Ollama is installed but not running. Start 'Ollama' from the Start menu, then run this script again." }
Say "Downloading the local model $model (one time, several GB)..."
& ollama pull $model
if ($LASTEXITCODE -ne 0) { Fail "Downloading $model failed. Check your internet connection and run this script again." }
$embedLine = Select-String -Path ".env" -Pattern "^NEXUS_EMBEDDING_MODEL=(.*)$" | Select-Object -First 1
$embed = if ($embedLine) { $embedLine.Matches[0].Groups[1].Value.Trim() } else { "" }
if (-not $embed) { $embed = "nomic-embed-text" }
Say "Downloading the search-by-meaning model $embed (one time, about 0.3 GB)..."
& ollama pull $embed
if ($LASTEXITCODE -ne 0) { Write-Host "Couldn't download $embed. Search will use words only until it's installed." -ForegroundColor Yellow }

# 6. Whisper speech model (downloads once, then works offline)
Say "Preparing Whisper for voice notes (one-time download)..."
& $venvPy -c "from app.services.transcription import LocalWhisperProvider as W; W().load(); print('Whisper ready')"
if ($LASTEXITCODE -ne 0) { Write-Host "Whisper isn't ready yet; voice notes will retry automatically. Text and images work." -ForegroundColor Yellow }

Say "All set. Start NEXUS any time by double-clicking  scripts\start_windows.bat"
Write-Host "(or run:  .venv\Scripts\python.exe run.py  and open http://localhost:8000)"
