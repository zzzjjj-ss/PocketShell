@echo off
rem ============================================================
rem  agent - install to user PATH
rem  Double-click to add this folder to the CURRENT USER's PATH,
rem  then open a NEW terminal and type: agent
rem  Safe: only touches HKCU\Environment\Path (user level).
rem ============================================================
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
endlocal
pause
