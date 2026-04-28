@echo off
REM One-click wrapper around pack_for_office.ps1.
REM Works from cmd.exe, PowerShell, VS Code terminal, or File Explorer
REM double-click. Always passes -ExecutionPolicy Bypass so the .ps1 runs
REM regardless of the user's PowerShell policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack_for_office.ps1" %*
