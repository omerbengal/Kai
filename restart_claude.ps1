# restart_claude.ps1
# Starts a new Claude Code session, confirms it launched, then kills the old claude.exe.
# Windows Terminal closes the tab automatically when claude.exe exits.

# Step 1: Snapshot currently-running Claude PIDs so we can detect the new one
$preLaunchPIDs = @(Get-Process -Name "claude" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)

# Step 2: Create sentinel so new session knows it was launched via this script
New-Item -ItemType File -Path "D:\Kai\tmp\restart_sentinel" -Force | Out-Null

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

# Step 5: Kill old claude.exe processes (deferred so this script can exit first).
# cmd.exe exits immediately after launching claude.exe, so we target claude directly.
# When claude.exe dies, Windows Terminal closes the tab automatically.
$oldPIDsList = $preLaunchPIDs -join ","
if ($oldPIDsList) {
    Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -Command `"Start-Sleep 2; foreach (`$p in @($oldPIDsList)) { Stop-Process -Id `$p -Force -ErrorAction SilentlyContinue }`"" -WindowStyle Hidden
} else {
    Write-Host "WARNING: No old claude.exe PIDs found - close the old window manually."
}
