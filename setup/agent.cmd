@echo off
rem agent command entry - run from any directory after install.bat
chcp 65001 >nul
setlocal
where python >nul 2>nul
if errorlevel 1 goto :py
python "%~dp0..\pocketshell\__main__.py" %*
goto :eof
:py
py "%~dp0..\pocketshell\__main__.py" %*
endlocal
