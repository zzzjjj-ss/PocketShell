@echo off
setlocal
chcp 65001 >nul
where python >nul 2>nul
if errorlevel 1 goto :py
python "%~dp0pocketshell\__main__.py" %*
goto :eof
:py
py "%~dp0pocketshell\__main__.py" %*
