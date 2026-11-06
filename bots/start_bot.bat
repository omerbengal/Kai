@echo off
:: Launch one Kai domain bot as its own Claude Code session (multi-bot stage 1, 05/11/2026).
::   D:\Kai\bots\start_bot.bat miluim
:: - Home is D:\KaiBots\<bot>, OUTSIDE D:\Kai: D:\Kai\CLAUDE.md and the main project hooks
::   (auto_ack, typing, session_start_cleanup, patch --session-start...) never load here.
:: - TELEGRAM_STATE_DIR gives the plugin its own token / access.json / bot.pid. KAI_BOT makes the
::   patched plugin refuse to start without it (it would otherwise take the MAIN bot's state).
:: - "/reload-plugins" as the trailing positional prompt: the channel does not auto-connect at
::   startup (upstream bug, investigation #038). Nothing may sit between it and the flags.
setlocal
set BOT=%~1
if "%BOT%"=="" (echo usage: start_bot.bat ^<bot^> & exit /b 1)
set HOME_DIR=D:\KaiBots\%BOT%
if not exist "%HOME_DIR%\telegram_state\.env" (echo no token yet: run bots\setup_bot.py first & exit /b 1)
if not exist "D:\Kai\config\telegram_topics_patch.on" (echo topics patch flag missing & exit /b 1)

robocopy "D:\Kai\bots\%BOT%" "%HOME_DIR%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo sync failed & exit /b 1)

cd /d "%HOME_DIR%"
:: A launch from inside a Claude session inherits its identity (05/11/2026): start clean.
set CLAUDECODE=
set CLAUDE_CODE_CHILD_SESSION=
set CLAUDE_CODE_ENTRYPOINT=
set CLAUDE_CODE_MESSAGING_SOCKET=
set CLAUDE_CODE_MESSAGING_TOKEN=
set CLAUDE_CODE_SESSION_ATTENDED=
set CLAUDE_CODE_SESSION_ID=
set CLAUDE_PID=
set KAI_BOT=%BOT%
set TELEGRAM_STATE_DIR=%HOME_DIR%\telegram_state
set CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe
title Kai-%BOT%
:: "& exit /b" stays on the launch line (cmd resumes a .bat by offset, see start_claude.bat)
"C:\Users\Omer\.local\bin\claude.exe" --model claude-opus-5-5 --effort medium --channels plugin:telegram@claude-plugins-official --dangerously-skip-permissions "/reload-plugins" & exit /b
::--------------------------------------------------------------------------
exit /b
