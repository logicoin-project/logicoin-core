@echo off
cd /d "%~dp0"
title Logicoin Wallet Backup
python logicoin_release_tools.py backup-wallet
echo.
pause
