@echo off
rem Gold Desk: double-click to start. MetaTrader 5 must be open and logged in on this PC.
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause
  exit /b 1
)
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
%PY% server.py %*
pause
