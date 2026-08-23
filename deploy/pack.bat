@echo off
REM One-click wrapper around pack.ps1. Works from cmd.exe, PowerShell, the
REM VS Code terminal, or a File Explorer double-click. Always passes
REM -ExecutionPolicy Bypass so the .ps1 runs regardless of machine policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1" %*
