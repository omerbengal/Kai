# kai_night.ps1 - the two halves of Kai's nightly downtime.
#
#   -Action Shutdown   runs at 02:00 (task KaiNightShutdown): stops Claude and the bridge
#   -Action Start      runs at 05:00 (task KaiMorningStart):  starts the bridge and Claude
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

    # Ignores the flag file. Only for testing a half by hand.
    [switch]$Force
)

$ErrorActionPreference = 'Continue'

# Closing the terminal WINDOW is separate from killing the process that lived in
# it: Stop-Process -Force exits non-zero, and Windows Terminal only auto-closes a
# window on a clean exit. See scripts\wt_window.ps1.
. D:\Kai\scripts\wt_window.ps1

$FlagFile = 'D:\Kai\tmp\night_mode_enabled'
$LogFile = 'D:\Kai\tmp\night_mode.log'
$BridgeBat = 'D:\Kai\start_whatsapp_bridge.bat'
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

function Get-BridgeChain {
    # Walks up from whatever is listening on 8080: main.exe <- go.exe <- cmd.exe.
    # Identifying the bridge by its port rather than by name matters because
    # 'main.exe' and 'go.exe' are generic names and their CommandLine is not
    # readable without elevation, so there is nothing else to match on safely.
    $chain = @()
    $pidListen = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
                 Select-Object -First 1 -ExpandProperty OwningProcess
    if (-not $pidListen) { return $chain }

    $cur = $pidListen
    for ($i = 0; $i -lt 4 -and $cur; $i++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        # Stop climbing past the cmd.exe that the .bat runs in. Anything above it
        # belongs to Task Scheduler or a terminal we must not kill.
        if ($proc.Name -notin @('main.exe', 'go.exe', 'cmd.exe')) { break }
        $chain += [pscustomobject]@{ Name = $proc.Name; Id = $proc.ProcessId }
        if ($proc.Name -eq 'cmd.exe') { break }
        $cur = $proc.ParentProcessId
    }
    return $chain
}

function Stop-Bridge {
    $chain = Get-BridgeChain
    if (-not $chain) {
        Write-Log "bridge: nothing listening on 8080, already down"
        return 0
    }
    foreach ($p in $chain) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Write-Log ("bridge: stopped {0} (pid {1})" -f $p.Name, $p.Id)
    }
    return $chain.Count
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

function Start-Bridge {
    # Clear any leftover first. start_whatsapp_bridge.bat only ever starts a new
    # bridge, so a survivor still holding 8080 would make the new one fail to bind.
    Stop-Bridge | Out-Null
    Start-Sleep -Seconds 2
    Start-Process 'cmd.exe' -ArgumentList '/c', $BridgeBat -WindowStyle Hidden
    Write-Log "bridge: launch issued"

    # 'go run' compiles before it listens, so give it real time before judging.
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $up = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
        if ($up) {
            Write-Log ("bridge: listening on 8080 after {0}s" -f (($i + 1) * 2))
            return $true
        }
    }
    Write-Log "bridge: FAILED to listen on 8080 within 120s"
    return $false
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

# ---------------------------------------------------------------- main

if (-not $Force -and -not (Test-Path $FlagFile)) {
    Write-Log "night mode is off (no $FlagFile), doing nothing"
    exit 0
}

if ($Action -eq 'Shutdown') {
    Write-Log "=== shutdown start ==="
    $c = Stop-Claude
    $b = Stop-Bridge
    Write-Log "=== shutdown done (claude procs: $c, bridge procs: $b) ==="
    Write-ActionLog 'night_shutdown' "stopped claude=$c bridge=$b"
    exit 0
}

Write-Log "=== start begin ==="
# Bridge first: it takes the longest (it compiles), and a session that comes up
# to a bridge already listening never sees a gap.
$bridgeOk = Start-Bridge
$claudeOk = Start-Claude
Write-Log "=== start done (bridge ok: $bridgeOk, claude ok: $claudeOk) ==="
Write-ActionLog 'night_start' "bridge_ok=$bridgeOk claude_ok=$claudeOk"

if (-not $bridgeOk -or -not $claudeOk) {
    # Leave the alerting to the watchdogs, which wake at 05:10 and will report a
    # genuine failure through the normal path rather than inventing a second one.
    Write-Log "one half did not come up; watchdogs resume at 05:10 and will handle it"
    exit 1
}
exit 0
