@echo off
rem Gold Desk on synthetic data with paper fills. No MetaTrader 5 needed.
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if not defined PY set PY=python
%PY% server.py --demo
pause
