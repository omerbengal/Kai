@echo off
cd /d D:\Kai
set CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe

:: Save this cmd window's PID so restart_claude.ps1 can close it later.
:: Wrapped in try/catch with 5s WMI timeout — if WMI hangs, do NOT block claude.exe launch.
powershell -NoProfile -Command "try { (Get-CimInstance Win32_Process -Filter \"ProcessId=$PID\" -OperationTimeoutSec 5 -ErrorAction Stop).ParentProcessId | Out-File 'D:\Kai\tmp\current_cmd_pid.txt' -Encoding ascii } catch { }" 2>nul

:: Clear the telegram plugin's entry in ~/.claude/mcp-needs-auth-cache.json before every start
:: (05/11/2026, Omer msg 22378). One failed plugin start - any session, a domain bot included -
:: lands there, and then EVERY later session skips the Telegram channel silently. This is the
:: one launch point shared by KaiRestart, restart_claude.ps1 and the 05:00 night start.
:: Removes only that key; exits 0 always; any error is ignored so the launch below always runs.
"C:\Users\Omer\AppData\Local\Programs\Python\Python39\python.exe" D:\Kai\scripts\kai_bots.py clear-auth-cache 2>nul

:: "/reload-plugins": channel plugins do not auto-connect at startup (upstream bug, #038).
:: "& exit /b" on the launch line: cmd resumes a .bat by byte offset after claude exits, so an
:: edit made mid-session re-ran the launch (02:00, 06/11). Keep it on the SAME line. The tail
:: line + exit below only stop sessions started from the previous version of this file.
"C:\Users\Omer\.local\bin\claude.exe" --model claude-opus-5-5 --effort medium --channels plugin:telegram@claude-plugins-official --dangerously-skip-permissions "/reload-plugins" & exit /b
::--------------------------------------------------------------------
exit /b
