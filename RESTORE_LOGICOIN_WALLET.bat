@echo off
cd /d "%~dp0"
title Logicoin Wallet Restore
echo Ziehe die Wallet-Backup-Datei in dieses Fenster oder gib den Pfad ein.
set /p BACKUP=Backup-Pfad: 
python logicoin_release_tools.py restore-wallet "%BACKUP%"
echo.
pause
