@echo off
cd /d "%~dp0"
title Logicoin Public Testnet Readiness
python logicoin_release_tools.py readiness
echo.
pause
