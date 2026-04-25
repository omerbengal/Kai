# restart_claude.ps1
# Starts a new Claude Code session, confirms it launched, then kills the old claude.exe.
# Windows Terminal closes the tab automatically when claude.exe exits.

# Step 1: Snapshot currently-running Claude PIDs so we can detect the new one
$preLaunchPIDs = @(Get-Process -Name "claude" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)

# Step 1b: Read OLD cmd PID NOW — before starting the new session overwrites this file
$cmdPidFile = "D:\Kai\tmp\current_cmd_pid.txt"
$oldCmdPID = $null
if (Test-Path $cmdPidFile) {
    $oldCmdPID = (Get-Content $cmdPidFile -Raw).Trim()
    Write-Host "Captured old cmd.exe PID: $oldCmdPID"
}

# Step 2: Write session handoff so the new session knows what was being discussed
Write-Host "Writing session handoff..."
python D:\Kai\scripts\write_session_handoff.py

# Step 2b: Create sentinel with the restart message ID so the new session can delete it
# If the caller already wrote the sentinel (e.g. a worker wrote ACK_MSG_ID directly), skip.
# Otherwise, wait for the async PostToolUse DB-save hook to complete, then query.
$restartMsgId = ""
if (Test-Path "D:\Kai\tmp\restart_sentinel") {
    $existing = (Get-Content "D:\Kai\tmp\restart_sentinel" -Raw).Trim()
    if ($existing) {
        $restartMsgId = $existing
        Write-Host "Sentinel already set to $restartMsgId by caller — skipping DB query"
    }
}
if (-not $restartMsgId) {
    # Wait for async PostToolUse hook to finish saving the reply message to DB
    Start-Sleep -Milliseconds 2000
    $pyScript = @'
import sqlite3
conn = sqlite3.connect('D:/Kai/kai.db')
conn.text_factory = str
cur = conn.cursor()
cur.execute('SELECT telegram_message_id FROM messages WHERE direction=? ORDER BY id DESC LIMIT 1', ('outgoing',))
row = cur.fetchone()
conn.close()
print(row[0] if row else '')
'@
    $pyScript | Out-File -FilePath "D:\Kai\tmp\_restart_query.py" -Encoding utf8 -NoNewline
    $restartMsgId = (python "D:\Kai\tmp\_restart_query.py").Trim()
}
if ($restartMsgId) {
    Set-Content -Path "D:\Kai\tmp\restart_sentinel" -Value $restartMsgId -NoNewline
    Write-Host "Saved restart message ID $restartMsgId to sentinel"
} else {
    New-Item -ItemType File -Path "D:\Kai\tmp\restart_sentinel" -Force | Out-Null
    Write-Host "No restart message ID found, created empty sentinel"
}

# Step 3: Start the new Claude session
Write-Host "Launching new Claude session..."
Start-Process "cmd.exe" `
    -ArgumentList "/k D:\Kai\start_claude.bat" `
    -WorkingDirectory "D:\Kai"

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

# Step 5: Kill old claude.exe processes AND their parent cmd.exe window.
# $oldCmdPID was captured in Step 1b (before the new session could overwrite the file).

$oldPIDsList = $preLaunchPIDs -join ","

if ($oldPIDsList -or $oldCmdPID) {
    # Build kill commands as a script block written to a temp file to avoid quoting issues
    $killLines = @("Start-Sleep -Seconds 2")
    foreach ($p in $preLaunchPIDs) {
        $killLines += "Stop-Process -Id $p -Force -ErrorAction SilentlyContinue"
    }
    if ($oldCmdPID) {
        $killLines += "Start-Sleep -Seconds 1"
        $killLines += "Stop-Process -Id $oldCmdPID -Force -ErrorAction SilentlyContinue"
    }
    $tmpScript = "$env:TEMP\kai_kill_$([System.Guid]::NewGuid().ToString('N')).ps1"
    $killLines | Set-Content -Path $tmpScript -Encoding UTF8
    Start-Process powershell -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-File", $tmpScript) -WindowStyle Hidden
    Write-Host "Kill script queued: $tmpScript (targeting PIDs: $oldPIDsList cmd:$oldCmdPID)"
} else {
    Write-Host "WARNING: No old claude.exe or cmd.exe PIDs found - close the old window manually."
}
