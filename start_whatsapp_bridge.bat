@echo off
cd /d D:\Kai\whatsapp-mcp\whatsapp-bridge
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Kai\scripts\kill_whatsapp_bridge.ps1
go run main.go >> D:\Kai\tmp\whatsapp-bridge.log 2>&1
