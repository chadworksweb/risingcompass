# Creates (or refreshes) a Desktop shortcut that launches the RC Track Visualizer.
# Self-contained + re-runnable: copies the RC favicon in, builds a .ico from it,
# and writes the .lnk to the Desktop. Mirrors the LEIT Control Panel shortcut.
$ErrorActionPreference = 'Stop'
$here    = $PSScriptRoot
$pngSrc  = Join-Path $here '..\..\frontend\img\favicon-192.png'
$pngDst  = Join-Path $here 'rc-viz-192.png'
$icoPath = Join-Path $here 'rc-viz.ico'

# 1. Bring the icon asset local so the visualizer folder is self-contained.
Copy-Item $pngSrc $pngDst -Force

# 2. Wrap the PNG in an ICO container (Windows Vista+ supports PNG-compressed
#    icon entries, so no rasterizing/resampling needed).
$png = [System.IO.File]::ReadAllBytes($pngDst)
$w = ([int]$png[16] -shl 24) -bor ([int]$png[17] -shl 16) -bor ([int]$png[18] -shl 8) -bor [int]$png[19]
$h = ([int]$png[20] -shl 24) -bor ([int]$png[21] -shl 16) -bor ([int]$png[22] -shl 8) -bor [int]$png[23]
$wb = if ($w -ge 256) { 0 } else { $w }
$hb = if ($h -ge 256) { 0 } else { $h }

$ms = New-Object System.IO.MemoryStream
$bw = New-Object System.IO.BinaryWriter($ms)
$bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]1)            # ICONDIR: reserved, type=icon, count=1
$bw.Write([byte]$wb); $bw.Write([byte]$hb); $bw.Write([byte]0); $bw.Write([byte]0)  # w, h, palette, reserved
$bw.Write([uint16]1); $bw.Write([uint16]32)                                 # planes, bitcount
$bw.Write([uint32]$png.Length); $bw.Write([uint32]22)                       # bytes in image, offset
$bw.Write($png)
$bw.Flush()
[System.IO.File]::WriteAllBytes($icoPath, $ms.ToArray())
$bw.Dispose(); $ms.Dispose()

# 3. Write the Desktop shortcut. Target = PowerShell running launch.ps1, so the
#    window stays open running the server; closing it stops the visualizer.
$desktop = Join-Path $env:USERPROFILE 'OneDrive\Desktop'
if (-not (Test-Path $desktop)) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
$lnk = Join-Path $desktop 'RC Track Visualizer.lnk'

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath       = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$sc.Arguments        = "-NoProfile -ExecutionPolicy Bypass -File `"$here\launch.ps1`""
$sc.WorkingDirectory = $here
$sc.IconLocation     = "$icoPath,0"
$sc.Description       = 'Rising Compass - parallel work-track visualizer'
$sc.WindowStyle      = 7   # minimized: the watchdog window tucks into the taskbar
$sc.Save()

Write-Host "Shortcut created: $lnk"
