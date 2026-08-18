@echo off
rem ============================================================
rem  PocketShell - install to user PATH
rem  Asks for a command name, generates <name>.cmd in setup\,
rem  then adds the setup folder to the CURRENT USER's PATH.
rem  New terminal -> type: <name> "your question"
rem  Safe: only touches HKCU\Environment\Path (user level).
rem ============================================================
setlocal
set "CMDNAME="
set /p CMDNAME=Command name [Enter=pocketshell]:
if "%CMDNAME%"=="" set CMDNAME=pocketshell
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -CmdName "%CMDNAME%"
endlocal
pause
