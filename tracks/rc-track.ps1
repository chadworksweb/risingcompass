<#
  rc-track.ps1 - Parallel work-track manager for Rising Compass.

  Each track is its own git worktree on its own branch (track/<name>), with its
  own backend.env / .deploy.env / .venv junction and its own pair of ports, so
  several agents (or terminals) can run full local stacks side by side without
  stepping on each other.

  Layout it creates:
    Local Sites/
      rising-compass/            <- main repo (this script lives in tracks/)
      rc-tracks/
        registry.json            <- local-only track -> slot/port/branch map
        <name>/                  <- a worktree, branch track/<name>

  Usage (run from anywhere; the script finds the main repo via its own path):
    pwsh ./tracks/rc-track.ps1 new   <name> [-From master]
    pwsh ./tracks/rc-track.ps1 start <name>
    pwsh ./tracks/rc-track.ps1 stop  <name>
    pwsh ./tracks/rc-track.ps1 sync  <name>          # rebase track onto origin/master
    pwsh ./tracks/rc-track.ps1 list
    pwsh ./tracks/rc-track.ps1 ports
    pwsh ./tracks/rc-track.ps1 viz
    pwsh ./tracks/rc-track.ps1 remove <name> [-DeleteBranch] [-Force]

  NOTE: the database is REMOTE and SHARED across every track. Pure code/frontend
  tracks run in parallel safely. Schema/migration work must happen on ONE track
  at a time. See plans and docs/RC-TRACKS-WORKFLOW.md.
#>

[CmdletBinding()]
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1)][string]$Name,
  [string]$From = "master",
  [switch]$DeleteBranch,
  [switch]$Force
)

$ErrorActionPreference = "Stop"

# --- paths -------------------------------------------------------------------
$MainRepo   = Split-Path $PSScriptRoot -Parent
$SitesRoot  = Split-Path $MainRepo -Parent
$TracksRoot = Join-Path $SitesRoot "rc-tracks"
$Registry   = Join-Path $TracksRoot "registry.json"

# Per-track scaffolding: gitignored files copied from main, and dirs junctioned.
$CopyFiles  = @(".deploy.env", "backend/.env")
$JunctionDirs = @("backend/.venv")

# --- helpers -----------------------------------------------------------------
function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host $m -ForegroundColor Green }
function Write-Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Die($m)        { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

function Load-Registry {
  if (-not (Test-Path $Registry)) { return @() }
  $raw = Get-Content $Registry -Raw
  if ([string]::IsNullOrWhiteSpace($raw)) { return @() }
  return @($raw | ConvertFrom-Json)
}

function Save-Registry($tracks) {
  if (-not (Test-Path $TracksRoot)) { New-Item -ItemType Directory -Path $TracksRoot -Force | Out-Null }
  ,@($tracks) | ConvertTo-Json -Depth 6 | Set-Content -Path $Registry -Encoding utf8
}

function Get-Track($tracks, $name) {
  return $tracks | Where-Object { $_.name -eq $name } | Select-Object -First 1
}

function Get-FreeSlot($tracks) {
  $used = @($tracks | ForEach-Object { $_.slot })
  for ($s = 1; $s -le 99; $s++) { if ($used -notcontains $s) { return $s } }
  Die "No free track slot (1-99 all in use)."
}

function Ports-ForSlot($slot) {
  return @{ backend = 8000 + ($slot * 10); frontend = 3005 + ($slot * 10) }
}

function Require-Name {
  if ([string]::IsNullOrWhiteSpace($Name)) { Die "This command needs a <name>." }
  if ($Name -notmatch '^[a-z0-9][a-z0-9-]*$') {
    Die "Name must be kebab-case (lowercase letters, digits, hyphens): got '$Name'."
  }
}

# --- commands ----------------------------------------------------------------
function Cmd-New {
  Require-Name
  $tracks = Load-Registry
  if (Get-Track $tracks $Name) { Die "Track '$Name' already exists. Use 'list' to see it." }

  $branch = "track/$Name"
  $exists = (git -C $MainRepo branch --list $branch)
  if ($exists) { Die "Branch '$branch' already exists. Pick another name or delete the branch." }

  $slot  = Get-FreeSlot $tracks
  $ports = Ports-ForSlot $slot
  $path  = Join-Path $TracksRoot $Name

  Write-Info "Creating track '$Name' (slot $slot) from '$From'..."
  Write-Info "  branch:   $branch"
  Write-Info "  worktree: $path"
  Write-Info "  backend:  http://127.0.0.1:$($ports.backend)"
  Write-Info "  frontend: http://127.0.0.1:$($ports.frontend)"

  git -C $MainRepo fetch origin --quiet 2>$null
  git -C $MainRepo worktree add -b $branch $path $From
  if ($LASTEXITCODE -ne 0) { Die "git worktree add failed." }

  # Scaffold gitignored per-checkout state.
  foreach ($rel in $CopyFiles) {
    $src = Join-Path $MainRepo $rel
    $dst = Join-Path $path $rel
    if (Test-Path $src) {
      $dstDir = Split-Path $dst -Parent
      if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
      Copy-Item $src $dst -Force
      Write-Ok  "  copied   $rel"
    } else {
      Write-Warn "  skipped  $rel (not present in main)"
    }
  }
  foreach ($rel in $JunctionDirs) {
    $src = Join-Path $MainRepo $rel
    $dst = Join-Path $path $rel
    if (Test-Path $src) {
      if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
      New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
      Write-Ok  "  junction $rel -> main"
    } else {
      Write-Warn "  skipped  $rel junction (not present in main)"
    }
  }

  # Per-track run notes.
  $trackMd = @"
# Track: $Name

- Branch:   $branch
- Worktree: $path
- Slot:     $slot
- Backend:  http://127.0.0.1:$($ports.backend)
- Frontend: http://127.0.0.1:$($ports.frontend)

## Run

Backend:
    cd "$path\backend"
    .\.venv\Scripts\uvicorn app.main:app --port $($ports.backend)

Frontend:
    cd "$path\frontend"
    python scripts/dev_server.py --port $($ports.frontend) --backend http://127.0.0.1:$($ports.backend)

Or, from the main repo: pwsh tracks/rc-track.ps1 start $Name

## Heads up
- The database is REMOTE and SHARED with every other track and with main.
  Do schema/migration work on ONE track only.
- .venv is junctioned to main: shared dependencies. If this track needs
  different deps, replace the junction with its own venv.
- .deploy.env / backend/.env were copied from main at creation time.
"@
  Set-Content -Path (Join-Path $path "TRACK.md") -Value $trackMd -Encoding utf8

  $tracks = @($tracks) + [pscustomobject]@{
    name = $Name; slot = $slot; branch = $branch; path = $path
    backendPort = $ports.backend; frontendPort = $ports.frontend
  }
  Save-Registry $tracks

  Write-Ok ""
  Write-Ok "Track '$Name' ready."
  Write-Info "Open it:    cd `"$path`""
  Write-Info "Run it:     pwsh `"$PSCommandPath`" start $Name"
  Write-Info "New agent:  open a Claude Code session with cwd = $path"
}

function Cmd-Start {
  Require-Name
  $t = Get-Track (Load-Registry) $Name
  if (-not $t) { Die "No track '$Name'. Run 'list'." }
  $be = "cd `"$($t.path)\backend`"; .\.venv\Scripts\uvicorn app.main:app --port $($t.backendPort)"
  $fe = "cd `"$($t.path)\frontend`"; python scripts/dev_server.py --port $($t.frontendPort) --backend http://127.0.0.1:$($t.backendPort)"
  Write-Info "Starting backend on :$($t.backendPort) and frontend on :$($t.frontendPort)..."
  Start-Process pwsh -ArgumentList @("-NoExit", "-Command", $be) | Out-Null
  Start-Process pwsh -ArgumentList @("-NoExit", "-Command", $fe) | Out-Null
  Write-Ok "Backend:  http://127.0.0.1:$($t.backendPort)"
  Write-Ok "Frontend: http://127.0.0.1:$($t.frontendPort)"
}

function Cmd-Stop {
  Require-Name
  $t = Get-Track (Load-Registry) $Name
  if (-not $t) { Die "No track '$Name'. Run 'list'." }
  foreach ($port in @($t.backendPort, $t.frontendPort)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $pids  = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($procId in $pids) {
      try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Ok "Stopped pid $procId on :$port" }
      catch { Write-Warn "Could not stop pid $procId on :$port" }
    }
    if (-not $pids) { Write-Warn "Nothing listening on :$port" }
  }
}

function Cmd-Sync {
  Require-Name
  $t = Get-Track (Load-Registry) $Name
  if (-not $t) { Die "No track '$Name'. Run 'list'." }
  Write-Info "Fetching origin and rebasing $($t.branch) onto origin/master..."
  git -C $t.path fetch origin --quiet
  git -C $t.path rebase origin/master
  if ($LASTEXITCODE -ne 0) {
    Write-Warn "Rebase hit conflicts. Resolve them in $($t.path), then 'git rebase --continue'."
  } else {
    Write-Ok "Track '$Name' is up to date with origin/master."
  }
}

function Cmd-List {
  $tracks = Load-Registry
  if (-not $tracks -or $tracks.Count -eq 0) { Write-Warn "No tracks yet. Create one: rc-track.ps1 new <name>"; }
  else {
    Write-Info "Tracks:"
    $tracks | Sort-Object slot | ForEach-Object {
      "{0,-20} slot {1,-3} be:{2}  fe:{3}  {4}" -f $_.name, $_.slot, $_.backendPort, $_.frontendPort, $_.branch | Write-Host
    }
  }
  Write-Host ""
  Write-Info "git worktrees:"
  git -C $MainRepo worktree list
}

function Cmd-Ports {
  Write-Info "Main:    backend 8000  frontend 3005"
  for ($s = 1; $s -le 8; $s++) {
    $p = Ports-ForSlot $s
    "slot {0}: backend {1}  frontend {2}" -f $s, $p.backend, $p.frontend | Write-Host
  }
}

function Cmd-Remove {
  Require-Name
  $tracks = Load-Registry
  $t = Get-Track $tracks $Name
  if (-not $t) { Die "No track '$Name'. Run 'list'." }

  # Drop the .venv junction first so worktree remove doesn't traverse into main.
  foreach ($rel in $JunctionDirs) {
    $dst = Join-Path $t.path $rel
    if (Test-Path $dst) { Remove-Item $dst -Recurse -Force -ErrorAction SilentlyContinue }
  }

  # The scaffolded files (.deploy.env, backend/.env, TRACK.md) are always
  # untracked and don't count as "work". Protect anything else unless -Force.
  $ignorable = @($CopyFiles + "TRACK.md" | ForEach-Object { $_.Replace('\','/') })
  $dirty = @(git -C $t.path status --porcelain) | Where-Object {
    $p = $_.Substring(3).Trim().Replace('\','/')
    $ignorable -notcontains $p
  }
  if ($dirty -and -not $Force) {
    Write-Warn "Track '$Name' has uncommitted work:"
    $dirty | ForEach-Object { Write-Host "  $_" }
    Die "Commit/stash it, or re-run with -Force to discard."
  }

  # Remove the scaffolded files so a clean tree removes without --force.
  foreach ($rel in $CopyFiles) {
    $f = Join-Path $t.path $rel
    if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
  }
  $tmd = Join-Path $t.path "TRACK.md"
  if (Test-Path $tmd) { Remove-Item $tmd -Force -ErrorAction SilentlyContinue }

  git -C $MainRepo worktree remove --force $t.path
  if ($LASTEXITCODE -ne 0) {
    Die "worktree remove failed. Check 'git worktree list' and remove manually."
  }
  Write-Ok "Removed worktree $($t.path)"

  if ($DeleteBranch) {
    git -C $MainRepo branch -D $t.branch
    if ($LASTEXITCODE -eq 0) { Write-Ok "Deleted branch $($t.branch)" }
    else { Write-Warn "Could not delete branch $($t.branch) (unmerged? delete manually with git branch -D)." }
  } else {
    Write-Warn "Kept branch $($t.branch). Delete it with -DeleteBranch or: git branch -D $($t.branch)"
  }

  $tracks = @($tracks | Where-Object { $_.name -ne $Name })
  Save-Registry $tracks
  Write-Ok "Track '$Name' removed from registry."
}

function Cmd-Viz {
  $launcher = Join-Path $PSScriptRoot "visualizer\launch.ps1"
  if (-not (Test-Path $launcher)) { Die "Visualizer not found at $launcher" }
  Write-Info "Starting the RC Track Visualizer..."
  Start-Process pwsh -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher) | Out-Null
  Write-Ok "Dashboard: http://127.0.0.1:4310 (opens in your browser once it is up)"
  Write-Info "Live view of every track: stack up/down, branch, dirty tree, ahead/behind, last commit."
}

function Cmd-Help {
  @"
rc-track.ps1 - parallel work-track manager for Rising Compass

  new   <name> [-From <branch>]   create branch track/<name> + worktree + env + ports
  start <name>                    launch backend + frontend for the track
  stop  <name>                    kill the track's backend + frontend processes
  sync  <name>                    fetch + rebase the track onto origin/master
  list                            show tracks and git worktrees
  ports                           show the slot -> port table
  viz                             open the live track visualizer dashboard
  remove <name> [-DeleteBranch] [-Force]   tear down a track

Each track is an isolated worktree/branch with its own ports. The database is
remote and shared: do migration work on one track at a time.
Full guide: plans and docs/RC-TRACKS-WORKFLOW.md
"@ | Write-Host
}

# --- dispatch ----------------------------------------------------------------
switch ($Command.ToLower()) {
  "new"    { Cmd-New }
  "start"  { Cmd-Start }
  "stop"   { Cmd-Stop }
  "sync"   { Cmd-Sync }
  "list"   { Cmd-List }
  "ports"  { Cmd-Ports }
  "viz"    { Cmd-Viz }
  "remove" { Cmd-Remove }
  "help"   { Cmd-Help }
  default  { Write-Warn "Unknown command '$Command'."; Cmd-Help }
}
