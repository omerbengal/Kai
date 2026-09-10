# run_restart_logged.ps1
# Thin wrapper around restart_claude.ps1 that captures ALL output (stdout, stderr,
# warnings, verbose) into D:\Kai\tmp\restart.log with a timestamped header per run.
#
# Why this exists (investigation, Aug 2026): the watchdog used to launch
# restart_claude.ps1 as a detached child, with its output going nowhere. When
# Task Scheduler tore down the watchdog's job object the ps1 died silently mid-prep
# and no terminal ever opened. With this wrapper the death point is observable
# instead of inferred.

$log = "D:\Kai\tmp\restart.log"
$stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")

try {
    Add-Content -Path $log -Value "" -Encoding UTF8
    Add-Content -Path $log -Value "===== RESTART RUN $stamp (pid $PID) =====" -Encoding UTF8
} catch { }

try {
    & "D:\Kai\restart_claude.ps1" *>> $log
    $code = $LASTEXITCODE
    Add-Content -Path $log -Value "===== RESTART RUN END $((Get-Date).ToString('HH:mm:ss')) exit=$code =====" -Encoding UTF8
} catch {
    Add-Content -Path $log -Value "===== RESTART RUN CRASHED: $($_.Exception.Message) =====" -Encoding UTF8
}

# Clear the watchdog's in-progress lock so the next cycle can act on reality.
try { Remove-Item "D:\Kai\tmp\restart_in_progress" -Force -ErrorAction SilentlyContinue } catch { }
