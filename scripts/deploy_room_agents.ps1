#Requires -Version 5.1
<#
.SYNOPSIS
  Install the Emma room agent on an EXPLICIT list of approved company PCs over
  WinRM, from this (management) machine, without walking to each one.

.DESCRIPTION
  Central deployment, the auditable way. It installs the agent on each machine
  NAMED in data/room-deploy.json -- a list you maintain by hand -- and does two
  things it deliberately never does the other way:

    * It does not DISCOVER targets. There is no ping sweep, no ARP, no subnet
      scan. The only machines it touches are the ones written in the list. A
      tool that finds its own targets is a tool nobody can audit.

    * The agent it installs cannot deploy itself onward. Deployment lives here,
      on the management machine, run by a person with credentials. An agent that
      installs the agent on the next machine is self-propagation -- a worm -- and
      is both riskier and far harder to control. The server names the targets.

  Every action is written to data/room-deploy-log/ with the operator's name and
  the result -- never a password, never a token.

  ONE-TIME PREREQUISITES on each target (by GPO for a domain, by hand for a
  workgroup of a few PCs):
    * PowerShell Remoting on:                 Enable-PSRemoting -Force
    * the deploying account is a local admin on the target
    * workgroup only: add the targets to this machine's TrustedHosts, and set
      LocalAccountTokenFilterPolicy=1 on each target so a local admin gets a
      full token over the network.
  If every PC is domain-joined, a GPO startup script or Intune/SCCM scales
  better than this; this is built for a LAN of a handful of machines.

.PARAMETER TargetsFile
  The explicit list. Default: data/room-deploy.json.

.PARAMETER Credential
  A local-admin account on the targets. Prompted for if omitted. Never stored.

.PARAMETER Uninstall
  Run uninstall.cmd on each target instead of installing.

.PARAMETER UseSSL
  Connect WinRM over HTTPS (5986) instead of HTTP.

.EXAMPLE
  # preview only, contacts nothing
  .\deploy_room_agents.ps1 -WhatIf

.EXAMPLE
  .\deploy_room_agents.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string] $TargetsFile = (Join-Path (Split-Path $PSScriptRoot -Parent) 'data\room-deploy.json'),
    [System.Management.Automation.PSCredential] $Credential,
    [switch] $Uninstall,
    [switch] $UseSSL
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path $PSScriptRoot -Parent
$PackagePy = Join-Path $PSScriptRoot 'make_room_package.py'
$RoomsFile = Join-Path $RepoRoot 'data\rooms.json'
$LogDir    = Join-Path $RepoRoot 'data\room-deploy-log'
$Python    = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

function Write-Utf8NoBom([string] $Path, [string] $Text) {
    # Windows PowerShell 5.1 writes a BOM through Out-File/>; rooms.json is read
    # as UTF-8 by scripts/room_control.py and the Thai room names must survive.
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Protect-AuditText([string] $Text) {
    # An audit row's 'result' is often an exception message, and one reachable
    # failure -- ConvertFrom-Json on a corrupt rooms.json -- can put a fragment of
    # that file into the message, and rooms.json is exactly where room TOKENS
    # live. So token shapes are redacted before anything is written, and the
    # message is truncated so a failure cannot dump a file into the log. The
    # log's contract is "never a token"; this is what enforces it.
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $t = [regex]::Replace($Text, 'ROOM_AGENT_TOKEN=\S+', 'ROOM_AGENT_TOKEN=<redacted>')
    $t = [regex]::Replace($t, '("token"\s*:\s*")[^"]*"', '$1<redacted>"')
    if ($t.Length -gt 300) { $t = $t.Substring(0, 300) + '...(truncated)' }
    return $t
}

function Write-Audit([hashtable] $Row) {
    # host, room, action, result, operator, time -- and NEVER a password or a
    # token. Same rule as the agent's own action log and data/logs/.
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    foreach ($k in @($Row.Keys)) {
        if ($Row[$k] -is [string]) { $Row[$k] = Protect-AuditText $Row[$k] }
    }
    $Row['at']       = (Get-Date).ToString('s')
    $Row['operator'] = $env:USERNAME
    $file = Join-Path $LogDir ((Get-Date).ToString('yyyy-MM-dd') + '.jsonl')
    Add-Content -Path $file -Value ($Row | ConvertTo-Json -Compress -Depth 4) -Encoding UTF8
}

function Assert-PlainHost([string] $Name) {
    # The list is trusted, but a host string flows into New-PSSession and the
    # audit log, so anything that is not a hostname or IP is refused here rather
    # than sent. Wildcards especially: a '*' must never fan out to many machines.
    if ($Name -notmatch '^[A-Za-z0-9_.-]{1,255}$') {
        throw "target host '$Name' is not a plain hostname or IP"
    }
}

# ---- the target list: explicit, hand-maintained, NEVER discovered ----
if (-not (Test-Path $TargetsFile)) {
    throw @"
no target list at $TargetsFile. Create it (gitignored -- it names internal PCs):
{
  "targets": [
    { "room": "ห้องขาย 1", "host": "192.168.1.51", "port": 8777 },
    { "room": "ห้องขาย 2", "host": "SALES-PC-2",   "port": 8777 }
  ]
}
"@
}
$targets = @((Get-Content $TargetsFile -Raw -Encoding UTF8 | ConvertFrom-Json).targets)
if ($targets.Count -eq 0) { throw "$TargetsFile has no 'targets'." }

# Validate the WHOLE list before touching anything, and normalise it. A single
# entry missing 'host' or 'room' used to throw mid-loop (StrictMode) and, because
# the read was outside the per-target try, abort the run -- every later approved
# machine silently skipped with no audit row. A deploy tool must not act on a
# list it cannot fully read, so a typo stops the run here, before ShouldProcess,
# before credentials, before any machine is contacted.
$plan = @()
$i = 0
foreach ($t in $targets) {
    $i++
    $names = $t.PSObject.Properties.Name
    foreach ($required in 'room', 'host') {
        if ($names -notcontains $required -or [string]::IsNullOrWhiteSpace([string] $t.$required)) {
            throw "target #$i in $TargetsFile has no '$required' -- fix the list and re-run"
        }
    }
    $plan += [pscustomobject]@{
        room = [string] $t.room
        host = [string] $t.host
        port = if ($names -contains 'port') { [int] $t.port } else { 8777 }
    }
}

$action = if ($Uninstall) { 'uninstall' } else { 'install' }
Write-Host "Plan: $action the room agent on $($plan.Count) machine(s), from $TargetsFile"
foreach ($t in $plan) {
    Write-Host ("  - {0,-14} {1}:{2}" -f $t.room, $t.host, $t.port)
}

if (-not $PSCmdlet.ShouldProcess("$($targets.Count) machine(s)", $action)) {
    Write-Host "WhatIf: nothing was contacted."
    return
}

if (-not $Credential) {
    $Credential = Get-Credential -Message "Local-admin account for the target PCs"
}

$remoteStage = 'C:\Windows\Temp\emma-deploy'

foreach ($t in $plan) {
    $roomHost = $t.host
    $room     = $t.room
    $port     = $t.port
    $session  = $null
    $stage    = $null    # local package dir; declared here so the finally can
                         # always clean it, even if a throw happens before it is set.

    try {
        Assert-PlainHost $roomHost
        Write-Host "`n== $room  ($roomHost) =="

        Test-WSMan -ComputerName $roomHost -ErrorAction Stop | Out-Null

        $sessArgs = @{ ComputerName = $roomHost; Credential = $Credential }
        if ($UseSSL) { $sessArgs.UseSSL = $true }
        $session = New-PSSession @sessArgs
        try {
            if ($Uninstall) {
                $r = Invoke-Command -Session $session -ScriptBlock {
                    $u = 'C:\ProgramData\EmmaRoomAgent\uninstall.cmd'
                    if (-not (Test-Path $u)) { return [pscustomobject]@{ code = 0; log = @('no agent installed here') } }
                    $env:ROOM_AGENT_SILENT = '1'   # uninstall.cmd would pause with no console under WinRM
                    $log = & cmd.exe /c "`"$u`"" 2>&1
                    [pscustomobject]@{ code = $LASTEXITCODE; log = @($log | Select-Object -Last 12) }
                }
                Write-Host ("  " + (@($r.log) -join "`n  "))
                # The agent is gone only if the scheduled task is gone.
                $stillThere = Invoke-Command -Session $session -ScriptBlock {
                    [bool](Get-ScheduledTask -TaskName 'EmmaRoomAgent' -ErrorAction SilentlyContinue)
                }
                if ($stillThere) { throw "uninstall did not complete on $roomHost (task still present, exit $($r.code))" }
                Write-Audit @{ host = $roomHost; room = $room; action = 'uninstall'; result = 'ok' }
            }
            else {
                # Build THIS room's package -- its own name and its own token --
                # locally, then push it. Each room is a different agent identity.
                $stage = Join-Path $env:TEMP ('emma-pkg-' + [guid]::NewGuid().ToString('N'))
                & $Python $PackagePy $room --out $stage --host '0.0.0.0' --port $port --allow all --programs all | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "packaging failed for $room" }

                Invoke-Command -Session $session -ArgumentList $remoteStage -ScriptBlock {
                    param($p)
                    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
                    New-Item -ItemType Directory -Path $p -Force | Out-Null
                }
                Copy-Item -ToSession $session -Path (Join-Path $stage '*') -Destination $remoteStage -Recurse -Force

                # The WinRM session already holds an elevated admin token, so
                # install.cmd's own admin check passes and it does not try to
                # raise a UAC prompt (there is no interactive desktop here).
                # ROOM_AGENT_SILENT stops install.cmd's `pause` from hanging.
                $r = Invoke-Command -Session $session -ArgumentList $remoteStage -ScriptBlock {
                    param($p)
                    $env:ROOM_AGENT_SILENT = '1'
                    $log = & cmd.exe /c "`"$p\install.cmd`"" 2>&1
                    [pscustomobject]@{ code = $LASTEXITCODE; log = @($log | Select-Object -Last 12) }
                }
                Write-Host ("  " + (@($r.log) -join "`n  "))

                # Do NOT trust a hardcoded 'ok'. install.cmd can bail (Python
                # missing -> exit 1) or, if the WinRM token is not elevated, hit
                # its self-elevation path and no-op with no desktop to prompt on.
                # Verify the POST-CONDITION on the target -- files in place AND the
                # scheduled task registered -- and only then write rooms.json. A
                # rooms.json entry pointing at an agent that never installed is
                # worse than a clear failure.
                $installed = Invoke-Command -Session $session -ScriptBlock {
                    (Test-Path 'C:\ProgramData\EmmaRoomAgent\run_room_agent.py') -and
                    [bool](Get-ScheduledTask -TaskName 'EmmaRoomAgent' -ErrorAction SilentlyContinue)
                }
                if (-not $installed) {
                    throw "install did not complete on $roomHost (exit $($r.code)); rooms.json left unchanged"
                }

                # Close the loop: this server now knows how to reach the room.
                $envText = Get-Content (Join-Path $stage '.env') -Raw -Encoding UTF8
                $token = ([regex]::Match($envText, '(?m)^ROOM_AGENT_TOKEN=(.*)$')).Groups[1].Value.Trim()
                $rooms = if (Test-Path $RoomsFile) {
                    Get-Content $RoomsFile -Raw -Encoding UTF8 | ConvertFrom-Json
                } else { [pscustomobject]@{} }
                $rooms | Add-Member -NotePropertyName $room -Force -NotePropertyValue (
                    [pscustomobject]@{ url = ("http://{0}:{1}" -f $roomHost, $port); token = $token })
                Write-Utf8NoBom $RoomsFile ($rooms | ConvertTo-Json -Depth 6)

                Write-Audit @{ host = $roomHost; room = $room; action = 'install'; result = 'ok'; port = $port }
                Write-Host "  installed; rooms.json updated. Live at the next logon on that PC."
            }
        }
        finally {
            # Both staging copies hold the room's .env with its token in plaintext,
            # so both are removed whether the deploy succeeded or threw.
            if ($session -and $session.State -eq 'Opened') {
                # Remote copy in a world-writable Temp: scrub it over the SAME
                # session (no second session to fail on its own), and if that
                # fails, SAY SO -- a leftover token copy on an endpoint must not
                # be swallowed by an empty catch.
                try {
                    Invoke-Command -Session $session -ArgumentList $remoteStage -ScriptBlock {
                        param($p) if (Test-Path $p) { Remove-Item $p -Recurse -Force }
                    }
                } catch {
                    Write-Warning "  $room ($roomHost): could not remove remote staging $remoteStage -- a token copy may remain there"
                    Write-Audit @{ host = $roomHost; room = $room; action = 'stage-cleanup'; result = ("failed: " + $_.Exception.Message) }
                }
                Remove-PSSession $session
            }
            # Local copy on this management machine.
            if ($stage -and (Test-Path $stage)) { Remove-Item $stage -Recurse -Force }
        }
    }
    catch {
        Write-Warning "  $room ($roomHost): $($_.Exception.Message)"
        Write-Audit @{ host = $roomHost; room = $room; action = $action; result = ("error: " + $_.Exception.Message) }
    }
}

Write-Host "`nDone. Audit log: $LogDir"
