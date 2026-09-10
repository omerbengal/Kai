# Starts the Kai live-voice server on http://127.0.0.1:8765 (project venv, never system Python 3.9).
# The access link with the secret key is printed on start. Stop with Ctrl+C.
$ErrorActionPreference = "Stop"
Set-Location "D:\Kai\voice"
& "D:\Kai\.venv\Scripts\python.exe" "D:\Kai\voice\server.py"
