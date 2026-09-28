# Start a built NEXUS.exe, check that it answers and works, then stop it. Used by the build workflow.
#   powershell -File packaging\smoke_windows.ps1 -Exe dist\NEXUS\NEXUS.exe
# Uses NEXUS_HOME (where its data goes) and NEXUS_PORT from the environment.
param([Parameter(Mandatory = $true)][string]$Exe)
$ErrorActionPreference = "Stop"
$port = if ($env:NEXUS_PORT) { $env:NEXUS_PORT } else { "8000" }
$base = "http://127.0.0.1:$port"
$proc = Start-Process $Exe -ArgumentList "--no-browser" -PassThru
function Show-Log {
    $log = Join-Path $env:NEXUS_HOME "data\nexus.log"
    if (Test-Path $log) { Write-Host "--- nexus.log ---"; Get-Content $log -Tail 80 }
}
try {
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) {
        if ($proc.HasExited) { Show-Log; throw "NEXUS exited early (code $($proc.ExitCode))" }
        try { $ping = Invoke-RestMethod "$base/api/ping" -TimeoutSec 2; $ok = $true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ok) { Show-Log; throw "NEXUS didn't answer within 90 s" }
    Write-Host "Answered: version $($ping.version) after $i s"
    $entry = Invoke-RestMethod "$base/api/entries" -Method Post -ContentType "application/json" `
        -Body '{"original_text": "Smoke test: a lighthouse keeper who wrestles at night", "analyze": false}'
    $found = Invoke-RestMethod "$base/api/search" -Method Post -ContentType "application/json" -Body '{"query": "lighthouse"}'
    if ($found.results.Count -lt 1) { throw "Search found nothing" }
    $page = Invoke-WebRequest "$base/" -UseBasicParsing
    if ($page.Content -notmatch "app.js") { throw "The page didn't load" }
    $sw = Invoke-WebRequest "$base/sw.js" -UseBasicParsing
    $new = Invoke-RestMethod "$base/api/whats-new"
    Write-Host "Captured entry $($entry.id), search works, page and service worker load, what's new has $($new.items.Count) items."
    if (-not (Test-Path (Join-Path $env:NEXUS_HOME "data\nexus.db"))) { throw "No database in NEXUS_HOME" }

    # Back up, change something, choose the backup to restore, restart from the page: the backup is back.
    Invoke-RestMethod "$base/api/backups" -Method Post | Out-Null
    for ($i = 0; $i -lt 60; $i++) { $b = Invoke-RestMethod "$base/api/backups"; if ($b.backups.Count -and -not $b.running) { break }; Start-Sleep -Seconds 1 }
    if (-not $b.backups.Count) { throw "No backup was made" }
    Invoke-RestMethod "$base/api/entries" -Method Post -ContentType "application/json" -Body '{"original_text": "Made after the backup", "analyze": false}' | Out-Null
    $zip = Join-Path $b.folder $b.backups[0].name
    $staged = Invoke-RestMethod "$base/api/backups/restore" -Method Post -Form @{ file = Get-Item $zip }
    if (-not $staged.restore_pending) { throw "The backup wasn't accepted for restoring" }
    if (-not (Invoke-RestMethod "$base/api/backups").can_restart) { throw "The app can't restart itself" }
    Invoke-RestMethod "$base/api/restart" -Method Post | Out-Null
    for ($i = 0; $i -lt 30; $i++) { try { Invoke-RestMethod "$base/api/ping" -TimeoutSec 1 | Out-Null; Start-Sleep -Milliseconds 300 } catch { break } }
    $back = $false
    for ($i = 0; $i -lt 90; $i++) { try { Invoke-RestMethod "$base/api/ping" -TimeoutSec 2 | Out-Null; $back = $true; break } catch { Start-Sleep -Seconds 1 } }
    if (-not $back) { Show-Log; throw "NEXUS didn't come back after restarting" }
    $last = (Invoke-RestMethod "$base/api/backups").last_restore
    if (-not $last.ok) { Show-Log; throw "The backup wasn't restored: $($last | ConvertTo-Json)" }
    $total = (Invoke-RestMethod "$base/api/entries").total
    if ($total -ne 1) { throw "Expected the 1 entry from the backup, found $total" }
    Write-Host "Restored a backup through Settings and restarted from the page; previous data kept in $($last.previous_data)."
} finally {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Get-Process NEXUS -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
