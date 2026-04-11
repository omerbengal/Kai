@echo off
cd /d D:\Kai
set CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe

:: Save this cmd window's PID so restart_claude.ps1 can close it later
powershell -NoProfile -Command "(Get-WmiObject Win32_Process -Filter \"ProcessId=$PID\").ParentProcessId | Out-File 'D:\Kai\tmp\current_cmd_pid.txt' -Encoding ascii"

"C:\Users\Omer\.local\bin\claude.exe" --channels plugin:telegram@claude-plugins-official --dangerously-skip-permissions --rc
