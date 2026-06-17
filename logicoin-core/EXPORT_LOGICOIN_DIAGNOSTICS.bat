@echo off
cd /d "%~dp0"
title Logicoin Diagnoseexport
python logicoin_release_tools.py diagnostics
echo.
pause
