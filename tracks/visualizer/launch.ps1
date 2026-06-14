# Launches the RC Track Visualizer and KEEPS IT ALIVE.
# One click on the Desktop icon and you are done: this opens the dashboard and
# then babysits the server, restarting it automatically if it ever stops. Tracks
# add/remove themselves (the dashboard reads live git worktrees), so you never
# start/stop/add/remove anything - you just watch.
#
# Close this window to stop the visualizer. That is the only manual control.
Set-Location $PSScriptRoot
$url = 'http://127.0.0.1:4310'

function Test-Up {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $url | Out-Null; return $true }
  catch { return $false }
}

# Already running (another window is the watchdog)? Just open the dashboard.
if (Test-Up) {
  Write-Host "RC Track Visualizer already running. Opening $url"
  Start-Process $url
  Start-Sleep -Milliseconds 600
  exit 0
}

# Open the browser once the server answers.
Start-Job -ArgumentList $url {
  param($url)
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $url | Out-Null; Start-Process $url; break }
    catch { Start-Sleep -Milliseconds 300 }
  }
} | Out-Null

Write-Host "RC Track Visualizer is running at $url"
Write-Host "It restarts itself if the server stops. Close this window to stop it."
Write-Host ""

# Watchdog: keep the server up until this window is closed.
while ($true) {
  try { node server.js } catch { Write-Host "node failed to launch: $_" -ForegroundColor Red }
  Write-Host ("[" + (Get-Date -Format 'HH:mm:ss') + "] server stopped - restarting in 2s (close this window to stop for good)") -ForegroundColor Yellow
  Start-Sleep -Seconds 2
}
