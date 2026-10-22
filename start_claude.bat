@echo off
cd /d D:\Kai
set CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe

:: Save this cmd window's PID so restart_claude.ps1 can close it later.
:: Wrapped in try/catch with 5s WMI timeout — if WMI hangs, do NOT block claude.exe launch.
powershell -NoProfile -Command "try { (Get-CimInstance Win32_Process -Filter \"ProcessId=$PID\" -OperationTimeoutSec 5 -ErrorAction Stop).ParentProcessId | Out-File 'D:\Kai\tmp\current_cmd_pid.txt' -Encoding ascii } catch { }" 2>nul

:: Workaround for a confirmed upstream Claude Code bug (investigation #038):
:: --channels plugin MCP servers do NOT auto-connect at startup on Windows
:: (they only connect on a manual /reload-plugins). Passing "/reload-plugins"
:: as the initial positional prompt makes CC run it the moment the session
:: starts, forcing the Telegram channel to connect with no manual action.
:: If CC ever fixes the upstream bug, this becomes a harmless no-op.
"C:\Users\Omer\.local\bin\claude.exe" --model claude-opus-5-5 --effort medium --channels plugin:telegram@claude-plugins-official --dangerously-skip-permissions "/reload-plugins"
