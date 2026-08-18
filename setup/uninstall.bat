@echo off
rem ============================================================
rem  agent - uninstall from user PATH
rem  Double-click to REMOVE this folder from the CURRENT USER's PATH.
rem  Deletes nothing; config.json / sessions / memory are kept.
rem ============================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
endlocal
pause
