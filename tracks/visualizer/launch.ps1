# Launches the RC Track Visualizer and opens the dashboard.
# - If a visualizer is already running, just opens the browser to it and exits.
# - Otherwise starts the server in THIS window (close it / Ctrl+C to stop) and
#   opens the browser once it is listening.
Set-Location $PSScriptRoot
$url = 'http://127.0.0.1:4310'

function Test-Up {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $url | Out-Null; return $true }
  catch { return $false }
}

if (Test-Up) {
  Write-Host "RC Track Visualizer already running. Opening $url"
  Start-Process $url
  Start-Sleep -Milliseconds 600
  exit 0
}

# Open the browser once the server we are about to start answers.
Start-Job -ArgumentList $url {
  param($url)
  for ($i = 0; $i -lt 50; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $url | Out-Null; Start-Process $url; break }
    catch { Start-Sleep -Milliseconds 300 }
  }
} | Out-Null

try {
  node server.js
} catch {
  Write-Host ""
  Write-Host "Failed to start the visualizer: $_" -ForegroundColor Red
  Read-Host "Press Enter to close"
}
