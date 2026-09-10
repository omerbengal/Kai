# Kill any running WhatsApp bridge (the process listening on 8080 and its "go run" parent).
# Must run elevated - the bridge is launched by the KaiWhatsAppBridge task at RunLevel Highest.
#
# Uses netstat rather than Get-NetTCPConnection: on this machine the CIM-backed
# cmdlet takes tens of seconds per call, which made the wait loop below hang for
# minutes and left a stuck cmd window behind on every restart attempt.
$ErrorActionPreference = 'SilentlyContinue'

function Get-Port8080Pids {
    $lines = netstat -ano | Select-String ':8080\s+.*LISTENING'
    $found = @()
    foreach ($l in $lines) {
        $parts = ($l.ToString().Trim() -split '\s+')
        $procId = $parts[-1]
        if ($procId -match '^\d+$' -and $procId -ne '0') { $found += [int]$procId }
    }
    return @($found | Sort-Object -Unique)
}

$pids = Get-Port8080Pids
foreach ($procId in $pids) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId"
    if ($proc -and $proc.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)"
        if ($parent -and $parent.Name -eq 'go.exe') {
            Stop-Process -Id $parent.ProcessId -Force
        }
    }
    Stop-Process -Id $procId -Force
}

# Wait up to 10s for the port to actually free up
for ($i = 0; $i -lt 10; $i++) {
    if (-not (Get-Port8080Pids)) { break }
    Start-Sleep -Seconds 1
}
