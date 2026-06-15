#!/usr/bin/env pwsh
# RC DB tunnel manager -- the SINGLE source of truth for the local -> droplet ->
# Postgres SSH tunnel. Called by BOTH the Claude SessionStart hook (auto-up when
# a session starts) and the Track Visualizer's tunnel control buttons.
#
# Security posture is UNCHANGED: the DB firewall still trusts only the droplet.
# This just automates the hop instead of typing it every session.
#
# Usage:
#   pwsh tracks/db-tunnel.ps1 status   # up? prints state, exit 0 = up / 1 = down
#   pwsh tracks/db-tunnel.ps1 up       # bring up if not already (idempotent)
#   pwsh tracks/db-tunnel.ps1 down     # tear down
#
# Host discovery (first hit wins) so nothing secret is committed:
#   1. $env:RC_DB_HOST                       (+ optional $env:RC_DB_SSH_TARGET)
#   2. tracks/db-tunnel.local.json           { "dbHost": "...", "sshTarget": "deploy@host" }
#   3. backend/.env  BACKUP_DATABASE_URL     (the direct DSN carries the real host)

[CmdletBinding()]
param([Parameter(Position = 0)][ValidateSet('status', 'up', 'down')][string]$Action = 'status')

$ErrorActionPreference = 'Stop'

$LocalPort  = 25061
$DefaultSsh = 'deploy@138.197.111.66'
$RepoRoot   = Split-Path -Parent (Split-Path -Parent $PSCommandPath)   # rising-compass/
$ConfigFile = Join-Path $PSScriptRoot 'db-tunnel.local.json'
$EnvFile    = Join-Path $RepoRoot 'backend/.env'

function Get-PortPid([int]$Port) {
    $line = netstat -ano -p tcp |
        Select-String -Pattern 'LISTENING' |
        Where-Object { $_ -match ":$Port\s" } |
        Select-Object -First 1
    if ($line -and ($line -match '(\d+)\s*$')) { return [int]$Matches[1] }
    return $null
}

function Test-TunnelUp { return [bool](Get-PortPid $LocalPort) }

function Resolve-Target {
    $dbHost = $env:RC_DB_HOST
    $ssh    = $env:RC_DB_SSH_TARGET

    if (-not $dbHost -and (Test-Path $ConfigFile)) {
        $cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($cfg.dbHost) { $dbHost = $cfg.dbHost }
        if (-not $ssh -and $cfg.sshTarget) { $ssh = $cfg.sshTarget }
    }

    if (-not $dbHost -and (Test-Path $EnvFile)) {
        foreach ($l in Get-Content $EnvFile) {
            if ($l -match 'BACKUP_DATABASE_URL\s*=.*@([^:/@]+):250\d\d') { $dbHost = $Matches[1]; break }
        }
    }

    if (-not $ssh) { $ssh = $DefaultSsh }
    if (-not $dbHost) {
        throw "DB host not found. Set RC_DB_HOST, or create $ConfigFile " +
              "with {`"dbHost`":`"<host>`"}, or ensure backend/.env has BACKUP_DATABASE_URL."
    }
    return @{ DbHost = $dbHost; SshTarget = $ssh }
}

switch ($Action) {
    'status' {
        if (Test-TunnelUp) { Write-Output "up (127.0.0.1:$LocalPort)"; exit 0 }
        Write-Output 'down'; exit 1
    }
    'down' {
        $procId = Get-PortPid $LocalPort
        if ($procId) {
            taskkill /PID $procId /F | Out-Null
            Write-Output "tunnel stopped (pid $procId)"
        } else {
            Write-Output 'already down'
        }
        exit 0
    }
    'up' {
        if (Test-TunnelUp) { Write-Output "already up (127.0.0.1:$LocalPort)"; exit 0 }
        $t   = Resolve-Target
        $fwd = "${LocalPort}:$($t.DbHost):$LocalPort"
        # No -f: launch a detached, hidden ssh that outlives this script, so the
        # forward persists across the session. Keepalives drop a dead link fast
        # (the visualizer/hook re-establishes on the next 'up').
        $sshArgs = @(
            '-N',
            '-o', 'ServerAliveInterval=30',
            '-o', 'ServerAliveCountMax=3',
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'StrictHostKeyChecking=accept-new',
            '-L', $fwd,
            $t.SshTarget
        )
        Start-Process -FilePath 'ssh' -ArgumentList $sshArgs -WindowStyle Hidden | Out-Null
        for ($i = 0; $i -lt 25; $i++) {
            Start-Sleep -Milliseconds 200
            if (Test-TunnelUp) { break }
        }
        if (Test-TunnelUp) {
            Write-Output "tunnel up (127.0.0.1:$LocalPort -> $($t.DbHost):$LocalPort via $($t.SshTarget))"
            exit 0
        }
        Write-Output "tunnel did NOT come up -- check SSH key/access to $($t.SshTarget)"
        exit 1
    }
}
