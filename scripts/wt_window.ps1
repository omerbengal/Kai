# wt_window.ps1 - closing the terminal WINDOW that hosted a Claude session.
#
# The problem this solves (2026-08-18):
#   Both shutdown paths (kai_night.ps1 -Action Shutdown, and restart_claude.ps1)
#   end a session with Stop-Process -Force on the cmd.exe hosting it. A forced
#   kill exits non-zero, and Windows Terminal's default closeOnExit is "graceful"
#   - it only closes a window when the shell exited cleanly. So the process dies,
#   the session dies, and an empty window titled "cmd.exe" stays on screen forever.
#   Omer had several of these stacked up.
#
# Why not the obvious fixes:
#   - Setting closeOnExit:"always" in the Windows Terminal settings would work, but
#     it is a GLOBAL change to how Omer's terminal behaves, to fix a Kai-only bug.
#   - wt.exe has no "close" verb (new-tab / split-pane / focus-tab / move-pane only).
#   - Killing the WindowsTerminal process is not an option: every Windows Terminal
#     window lives inside ONE process, so killing it takes the live session with it.
#
# What we do instead: remember the HWND of the window we opened, and post WM_CLOSE
# to exactly that window when we tear the session down. Same pattern as
# tmp\current_cmd_pid.txt, one level up - that file remembers the process, this one
# remembers the window.
#
# Dot-source it:  . D:\Kai\scripts\wt_window.ps1

$script:WtHwndFile = 'D:\Kai\tmp\current_wt_hwnd.txt'

if (-not ('KaiWin' -as [type])) {
    Add-Type @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class KaiWin {
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);

    public const uint WM_CLOSE = 0x0010;

    // A console process hosted inside Windows Terminal still owns a 0x0
    // PseudoConsoleWindow that EnumWindows reports as visible. It is not a real
    // window, nobody can see it, and closing it is meaningless - skip it.
    public static bool HasRealSize(IntPtr h) {
        RECT r;
        if (!GetWindowRect(h, out r)) return false;
        return (r.Right - r.Left) > 0 && (r.Bottom - r.Top) > 0;
    }

    // "hwnd|pid|title" per visible top-level window - kept as strings so the
    // PowerShell side never has to marshal a custom type across Add-Type.
    public static List<string> ListVisible() {
        var found = new List<string>();
        EnumWindows((h, p) => {
            if (IsWindowVisible(h) && HasRealSize(h)) {
                var sb = new StringBuilder(512);
                GetWindowText(h, sb, 512);
                uint pid;
                GetWindowThreadProcessId(h, out pid);
                found.Add(h.ToInt64() + "|" + pid + "|" + sb.ToString());
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@
}

# Every visible top-level window that belongs to a terminal host.
# WindowsTerminal covers the normal path; the others cover the classic-console
# fallback in restart_claude.ps1 for machines with no wt.exe.
function Get-KaiTerminalWindows {
    $hosts = @('WindowsTerminal', 'OpenConsole', 'conhost', 'cmd')
    $out = @()
    foreach ($entry in [KaiWin]::ListVisible()) {
        $parts = $entry.Split('|', 3)
        $hwnd = [int64]$parts[0]
        $procId = [int]$parts[1]
        $title = $parts[2]
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc -and $hosts -contains $proc.ProcessName) {
            $out += [pscustomobject]@{ Hwnd = $hwnd; Pid = $procId; Name = $proc.ProcessName; Title = $title }
        }
    }
    return $out
}

# Reads the HWND recorded for the CURRENT session, then deletes the file.
# Read-once on purpose: a stale handle is worse than no handle, because window
# handles get recycled and we would eventually post WM_CLOSE to a stranger.
function Read-KaiTerminalHwnd {
    if (-not (Test-Path $script:WtHwndFile)) { return $null }
    $raw = (Get-Content $script:WtHwndFile -Raw -ErrorAction SilentlyContinue)
    Remove-Item $script:WtHwndFile -Force -ErrorAction SilentlyContinue
    if (-not $raw) { return $null }
    $raw = $raw.Trim()
    $parsed = 0L
    if (-not [int64]::TryParse($raw, [ref]$parsed)) { return $null }
    if ($parsed -le 0) { return $null }
    return $parsed
}

function Write-KaiTerminalHwnd([int64]$Hwnd) {
    Set-Content -Path $script:WtHwndFile -Value $Hwnd -Encoding ascii -NoNewline
}

# Diffs two Get-KaiTerminalWindows snapshots and returns the window that appeared.
# If more than one appeared (Omer opened his own terminal in the same second),
# return nothing rather than guess - a wrong handle closes the wrong window.
#
# The WindowsTerminal-first pass is not cosmetic: launching one wt.exe window also
# makes a phantom top-level cmd window show up in EnumWindows, so a naive
# "exactly one new window" rule sees two and gives up every single time.
function Find-NewKaiTerminalWindow($Before, $After) {
    $beforeHandles = @($Before | Select-Object -ExpandProperty Hwnd)
    $new = @($After | Where-Object { $beforeHandles -notcontains $_.Hwnd })
    $wt = @($new | Where-Object { $_.Name -eq 'WindowsTerminal' })
    if ($wt.Count -eq 1) { return $wt[0] }
    if ($wt.Count -eq 0 -and $new.Count -eq 1) { return $new[0] }
    return $null
}

# Posts WM_CLOSE to a specific window, but only after re-confirming it is still a
# live, visible terminal window. Between recording the handle and using it the
# window may have been closed by hand and its handle handed to something else;
# the re-check is what keeps this from closing an unrelated app.
function Close-KaiTerminalWindow([int64]$Hwnd) {
    if (-not $Hwnd) { return $false }
    $ptr = [IntPtr]$Hwnd
    if (-not [KaiWin]::IsWindow($ptr)) { return $false }
    if (-not [KaiWin]::IsWindowVisible($ptr)) { return $false }
    if (-not [KaiWin]::HasRealSize($ptr)) { return $false }

    $procId = 0
    [void][KaiWin]::GetWindowThreadProcessId($ptr, [ref]$procId)
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if (@('WindowsTerminal', 'OpenConsole', 'conhost', 'cmd') -notcontains $proc.ProcessName) { return $false }

    [void][KaiWin]::PostMessage($ptr, [KaiWin]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
    return $true
}
