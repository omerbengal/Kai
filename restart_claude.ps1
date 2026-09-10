# restart_claude.ps1
# Starts a new Claude Code session, confirms it launched, then kills the old claude.exe.
# Windows Terminal closes the tab automatically when claude.exe exits.

# Step 0: Window helpers. A forced kill of cmd.exe leaves the Windows Terminal
# window standing (closeOnExit defaults to "graceful"), so we close the window
# explicitly by handle. See scripts\wt_window.ps1 for the full reasoning.
. D:\Kai\scripts\wt_window.ps1

# Step 1: Snapshot currently-running Claude PIDs so we can detect the new one
$preLaunchPIDs = @(Get-Process -Name "claude" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)

# Step 1b: Read OLD cmd PID NOW — before starting the new session overwrites this file
$cmdPidFile = "D:\Kai\tmp\current_cmd_pid.txt"
$oldCmdPID = $null
if (Test-Path $cmdPidFile) {
    $oldCmdPID = (Get-Content $cmdPidFile -Raw).Trim()
    Write-Host "Captured old cmd.exe PID: $oldCmdPID"
}

# Step 1c: Read the OLD session's window handle now, for the same reason as 1b —
# launching the new session is what overwrites the file. Read-once: the helper
# deletes it on read so a stale handle can never be reused later.
$oldWtHwnd = Read-KaiTerminalHwnd
if ($oldWtHwnd) { Write-Host "Captured old terminal window handle: $oldWtHwnd" }

# Step 2: Write session handoff so the new session knows what was being discussed
Write-Host "Writing session handoff..."
python D:\Kai\scripts\write_session_handoff.py

# Step 2b: Create sentinel with the restart message ID so the new session can delete it
# If the caller already wrote the sentinel (e.g. a worker wrote ACK_MSG_ID directly), skip.
# If nobody wrote it, the sentinel stays empty and the new session deletes nothing.
$restartMsgId = ""
if (Test-Path "D:\Kai\tmp\restart_sentinel") {
    $existing = (Get-Content "D:\Kai\tmp\restart_sentinel" -Raw).Trim()
    if ($existing) {
        $restartMsgId = $existing
        Write-Host "Sentinel already set to $restartMsgId by caller — nothing to guess"
    }
}
# NOTE: there is deliberately NO fallback here.
# A previous version queried the DB for the last outgoing message and assumed it was
# the restart notice. When a restart happened with no restart notice (e.g. the nightly
# night-mode restart), that heuristic picked an unrelated real message - and
# session_start_cleanup.py then deleted it from both the chat and the messages table.
# See the reminder-nag #11439 incident (2026-08-12). If no caller wrote the sentinel,
# we delete nothing.
if ($restartMsgId) {
    Set-Content -Path "D:\Kai\tmp\restart_sentinel" -Value $restartMsgId -NoNewline
    Write-Host "Saved restart message ID $restartMsgId to sentinel"
} else {
    New-Item -ItemType File -Path "D:\Kai\tmp\restart_sentinel" -Force | Out-Null
    Write-Host "No restart message ID found, created empty sentinel"
}

# Step 2c: Pre-patch the Telegram plugin BEFORE Claude Code launches.
# Why: Claude Code spawns MCP plugins at startup BEFORE SessionStart hooks fire.
# The SessionStart hook patches the source on disk, but the plugin process is already
# running with un-patched code in memory, requiring a manual /reload-plugins.
# Running the patch here ensures the on-disk source is already patched when MCP boots.
# The script is idempotent (checks markers before applying) so the SessionStart hook
# remains as belt-and-suspenders.
Write-Host "Pre-patching Telegram plugin..."
python D:\Kai\scripts\patch_telegram_plugin.py

# Step 3: Start the new Claude session
Write-Host "Launching new Claude session..."
$windowsBefore = Get-KaiTerminalWindows
$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if ($wt) {
    Start-Process "wt.exe" `
        -ArgumentList "-d", "D:\Kai", "cmd.exe", "/k", "D:\Kai\start_claude.bat"
} else {
    Start-Process "cmd.exe" `
        -ArgumentList "/k", "D:\Kai\start_claude.bat" `
        -WorkingDirectory "D:\Kai"
}

# Step 4: Poll for a new Claude process (up to 30 seconds)
$timeout = 30
$elapsed = 0
$confirmed = $false
$newClaude = $null

while ($elapsed -lt $timeout) {
    Start-Sleep -Seconds 2
    $elapsed += 2
    $newClaude = Get-Process -Name "claude" -ErrorAction SilentlyContinue |
                 Where-Object { $preLaunchPIDs -notcontains $_.Id }
    if ($newClaude) {
        $confirmed = $true
        break
    }
}

if (-not $confirmed) {
    Write-Host "ERROR: New Claude session did not appear within $timeout seconds. Current session NOT closed."
    exit 1
}

Write-Host "New session confirmed (PID $($newClaude.Id)). Closing current window..."

# Step 4b: Record the window the new session came up in, so whoever tears this
# session down next (the nightly shutdown, or the next restart) can close it.
# If two terminal windows appeared at once we record nothing rather than guess —
# no handle just means the old behaviour, a wrong handle closes Omer's own window.
$newWindow = Find-NewKaiTerminalWindow $windowsBefore (Get-KaiTerminalWindows)
if ($newWindow) {
    Write-KaiTerminalHwnd $newWindow.Hwnd
    Write-Host "Recorded new terminal window handle: $($newWindow.Hwnd) ($($newWindow.Name))"
} else {
    Write-Host "Could not identify the new terminal window - its handle will not be recorded."
}

# Step 5: Kill old claude.exe processes AND their parent cmd.exe window.
# $oldCmdPID was captured in Step 1b (before the new session could overwrite the file).

$oldPIDsList = $preLaunchPIDs -join ","

# Step 5a: Find the old session's CHILD telegram plugin processes (bun/node).
# Investigation #034: killing old claude.exe orphans its child bun, which keeps
# long-polling Telegram getUpdates on the same bot token and STEALS incoming
# messages from the new session (Telegram delivers each update to one poller only).
# Capture them NOW (while parents are still alive) and kill them alongside the old
# claude so no orphan poller survives.
$oldPluginPIDs = @()
if ($preLaunchPIDs) {
    $oldPluginPIDs = @(
        Get-CimInstance Win32_Process -Filter "Name='bun.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { ($preLaunchPIDs -contains $_.ParentProcessId) -and ($_.CommandLine -like '*claude-plugins-official*telegram*') } |
        Select-Object -ExpandProperty ProcessId
    )
    if ($oldPluginPIDs) { Write-Host "Old-session telegram plugin child PIDs to kill: $($oldPluginPIDs -join ',')" }
}

if ($oldPIDsList -or $oldCmdPID -or $oldPluginPIDs -or $oldWtHwnd) {
    # Build kill commands as a script block written to a temp file to avoid quoting issues
    $killLines = @("Start-Sleep -Seconds 2")
    foreach ($p in $preLaunchPIDs) {
        $killLines += "Stop-Process -Id $p -Force -ErrorAction SilentlyContinue"
    }
    foreach ($p in $oldPluginPIDs) {
        $killLines += "Stop-Process -Id $p -Force -ErrorAction SilentlyContinue"
    }
    if ($oldCmdPID) {
        $killLines += "Start-Sleep -Seconds 1"
        $killLines += "Stop-Process -Id $oldCmdPID -Force -ErrorAction SilentlyContinue"
    }
    # The window goes last: cmd.exe must already be gone, otherwise Windows Terminal
    # answers WM_CLOSE with a "close anyway?" prompt for the still-running shell.
    if ($oldWtHwnd) {
        $killLines += "Start-Sleep -Seconds 1"
        $killLines += ". D:\Kai\scripts\wt_window.ps1"
        $killLines += "Close-KaiTerminalWindow $oldWtHwnd | Out-Null"
    }
    $tmpScript = "$env:TEMP\kai_kill_$([System.Guid]::NewGuid().ToString('N')).ps1"
    $killLines | Set-Content -Path $tmpScript -Encoding UTF8
    Start-Process powershell -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-File", $tmpScript) -WindowStyle Hidden
    Write-Host "Kill script queued: $tmpScript (targeting PIDs: $oldPIDsList cmd:$oldCmdPID)"
} else {
    Write-Host "WARNING: No old claude.exe or cmd.exe PIDs found - close the old window manually."
}
