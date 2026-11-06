# kai_night.ps1 - the two halves of Kai's nightly downtime.
#
#   -Action Shutdown   runs at 02:00 (task KaiNightShutdown): stops the domain bots and Claude
#   -Action Start      runs at 05:00 (task KaiMorningStart):  starts Claude and the bots
#
# The WhatsApp bridge is NOT part of the night (Omer, 06/11/2026): the stop exists to clear
# Claude's context and the bridge has none. It never really stopped anyway - it runs elevated
# (KaiWhatsAppBridge, HighestAvailable) and Stop-Process from this LeastPrivilege task failed
# silently while the log said "stopped". KaiWhatsAppWatchdog guards it around the clock.
#
# Both halves refuse to do anything unless D:\Kai\tmp\night_mode_enabled exists.
# Deleting that one file is the off switch: the tasks still fire, see the flag is
# gone, log "night mode is off" and exit without touching a single process.
# The watchdogs read the same flag from scripts\night_mode.py, so the whole feature
# turns on and off together.
#
# To remove night mode completely instead of just pausing it, run
# D:\Kai\rollback\night_mode_2026-08-11\ROLLBACK.ps1.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Shutdown', 'Start')]
    [string]$Action,

    # Ignores the flag file AND the local-time slot gate. Only for running a half by hand.
    [switch]$Force,

    # Evaluate the flag + slot gate, log the decision, and exit before touching any process.
    [switch]$DryRun
)

# Travel mode (2026-09-13): the tasks fire every 30 minutes. Whether now is the
# local 02:00 / 05:00 slot - and whether this half already ran tonight - is
# decided by scripts\night_mode.py --slot, using scripts\local_tz.py.

$ErrorActionPreference = 'Continue'

# Closing the terminal WINDOW is separate from killing the process that lived in
# it: Stop-Process -Force exits non-zero, and Windows Terminal only auto-closes a
# window on a clean exit. See scripts\wt_window.ps1.
. D:\Kai\scripts\wt_window.ps1

$FlagFile = 'D:\Kai\tmp\night_mode_enabled'
$LogFile = 'D:\Kai\tmp\night_mode.log'
$Python = 'C:\Users\Omer\AppData\Local\Programs\Python\Python39\python.exe'

function Write-Log([string]$msg) {
    $line = "{0} [night/{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Action.ToLower(), $msg
    Write-Output $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

function Write-ActionLog([string]$action, [string]$details) {
    # Mirrors the watchdogs' audit trail so a night can be reconstructed from the DB alone.
    $py = @"
import sqlite3
c = sqlite3.connect(r'D:\Kai\kai.db')
c.text_factory = str
c.execute("INSERT INTO action_log (agent, action, details) VALUES (?, ?, ?)",
          ('night_mode', '$action', '''$details'''))
c.commit()
c.close()
"@
    $tmp = Join-Path $env:TEMP ("kai_night_log_" + [Guid]::NewGuid().ToString('N') + ".py")
    try {
        $py | Out-File -FilePath $tmp -Encoding utf8
        & $Python $tmp | Out-Null
    } catch {
        Write-Log "could not write action_log: $_"
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Stop-Claude {
    $claude = @(Get-Process -Name 'claude' -ErrorAction SilentlyContinue)
    if (-not $claude) {
        Write-Log "claude: not running, nothing to stop"
        return 0
    }
    $claudePIDs = @($claude | Select-Object -ExpandProperty Id)

    # Investigation #034: an orphaned telegram plugin keeps long-polling getUpdates
    # on the same bot token and steals messages from the next session. Kill every
    # plugin process, not just children of the claude we are stopping, so nothing
    # survives the night holding the poll.
    $plugins = @(
        Get-CimInstance Win32_Process -Filter "Name='bun.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $claudePIDs -contains $_.ParentProcessId } |
        Select-Object -ExpandProperty ProcessId
    )

    # The cmd.exe window hosting the session, recorded by start_claude.bat.
    $cmdPid = $null
    if (Test-Path 'D:\Kai\tmp\current_cmd_pid.txt') {
        $cmdPid = (Get-Content 'D:\Kai\tmp\current_cmd_pid.txt' -Raw).Trim()
    }
    # ...and the terminal window it is drawn in, recorded by restart_claude.ps1.
    $wtHwnd = Read-KaiTerminalHwnd

    foreach ($p in $plugins) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Log "claude: stopped telegram plugin child (pid $p)"
    }
    foreach ($p in $claudePIDs) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Log "claude: stopped claude.exe (pid $p)"
    }
    if ($cmdPid) {
        Start-Sleep -Seconds 1
        Stop-Process -Id $cmdPid -Force -ErrorAction SilentlyContinue
        Write-Log "claude: stopped host shell (pid $cmdPid)"
    }
    # Window last, and only once the shell inside it is gone - Windows Terminal
    # puts up a "close anyway?" prompt if a process is still running in the pane,
    # and a prompt at 02:00 with nobody awake is exactly the stuck window we are
    # trying to get rid of.
    if ($wtHwnd) {
        Start-Sleep -Seconds 1
        if (Close-KaiTerminalWindow $wtHwnd) {
            Write-Log "claude: closed terminal window (hwnd $wtHwnd)"
        } else {
            Write-Log "claude: terminal window $wtHwnd was already gone or is no longer a terminal - left alone"
        }
    } else {
        Write-Log "claude: no terminal window handle recorded, window not closed"
    }
    return $claudePIDs.Count
}

function Start-Claude {
    # Reuse the path that is already proven in production: KaiRestart runs
    # run_restart_logged.ps1 -> restart_claude.ps1, which launches a session,
    # confirms it appeared, and writes to tmp\restart.log. With no old session
    # running it is simply a start, since it only kills PIDs captured beforehand.
    schtasks /run /tn KaiRestart | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "claude: FAILED to trigger KaiRestart (exit $LASTEXITCODE)"
        return $false
    }
    Write-Log "claude: KaiRestart triggered"

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if (Get-Process -Name 'claude' -ErrorAction SilentlyContinue) {
            Write-Log ("claude: up after {0}s" -f (($i + 1) * 2))
            return $true
        }
    }
    Write-Log "claude: FAILED to appear within 120s"
    return $false
}

function Invoke-Bots([string]$verb, [switch]$Dry) {
    # Domain bots (config\bots.json, multi-bot stage 1, 05/11/2026). Each bot is its own
    # Claude session launched by its own task (KaiBot<Name>, clean env). stop-all kills only
    # PIDs whose session cwd is the bot home; start-all clears the needs-auth cache trap and
    # runs the task. Never fatal for main Kai: any failure is logged and the night goes on.
    $botArgs = @('D:\Kai\scripts\kai_bots.py', $verb)
    if ($Dry) { $botArgs += '--dry-run' }
    try {
        $out = & $Python @botArgs 2>&1
        $rc = $LASTEXITCODE
        foreach ($l in @($out)) { Write-Log "bots: $l" | Write-Host }
        return ($rc -eq 0)
    } catch {
        Write-Log "bots: $verb threw: $_" | Write-Host
        return $false
    }
}

# Main Kai's start needs ~20s for its /reload-plugins workaround before a second
# session joins; bots start after that.
$BotStartDelaySeconds = 20

# ---------------------------------------------------------------- main

if (-not $Force) {
    # Slot gate first: 46 of the 48 daily firings are outside the slot and must stay silent.
    $slotArgs = @('D:\Kai\scripts\night_mode.py', '--slot', $Action)
    if ($DryRun) { $slotArgs += '--dry-run' }
    $gate = & $Python @slotArgs 2>&1
    $gateExit = $LASTEXITCODE
    if ($gateExit -ne 0) {
        if ($gateExit -eq 3 -and "$gate" -match 'not_in_slot') {
            if ($DryRun) { Write-Output "dry-run: $gate" }
            exit 0
        }
        Write-Log "slot gate: $gate (exit $gateExit), doing nothing"
        exit 0
    }
    if (-not (Test-Path $FlagFile)) {
        Write-Log "night mode is off (no $FlagFile), doing nothing [$gate]"
        exit 0
    }
    Write-Log "slot gate: $gate"
}

if ($DryRun) {
    Write-Log "dry-run: would $Action now, exiting without touching any process"
    if ($Action -eq 'Shutdown') { Invoke-Bots 'stop-all' -Dry | Out-Null } else { Invoke-Bots 'start-all' -Dry | Out-Null }
    exit 0
}

if ($Action -eq 'Shutdown') {
    Write-Log "=== shutdown start ==="
    # Bots first, with their whole process tree; Stop-Claude below kills every claude.exe and
    # would otherwise leave a bot's plugin grandchild polling its token.
    Invoke-Bots 'stop-all' | Out-Null
    $c = Stop-Claude
    Write-Log "=== shutdown done (claude procs: $c) ==="
    Write-ActionLog 'night_shutdown' "stopped claude=$c"
    exit 0
}

Write-Log "=== start begin ==="
$claudeOk = Start-Claude
Start-Sleep -Seconds $BotStartDelaySeconds
$botsOk = Invoke-Bots 'start-all'
Write-Log "=== start done (claude ok: $claudeOk, bots ok: $botsOk) ==="
Write-ActionLog 'night_start' "claude_ok=$claudeOk bots_ok=$botsOk"

if (-not $claudeOk) {
    # Leave the alerting to the watchdogs, which wake at 05:10 and will report a
    # genuine failure through the normal path rather than inventing a second one.
    Write-Log "claude did not come up; the watchdog resumes at 05:10 and will handle it"
    exit 1
}
exit 0
