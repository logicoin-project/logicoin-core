@echo off
title Logicoin LOGIC Multi-GPU Starter v0.12.15.3
cd /d "%~dp0"
echo Starte GPU 0 und GPU 1 in getrennten Fenstern...
start "LOGIC GPU0" cmd /k call start_logicoin_gpu_miner_gpu0.bat
timeout /t 2 >nul
start "LOGIC GPU1" cmd /k call start_logicoin_gpu_miner_gpu1.bat
echo Fertig. Zwei Miner-Fenster sollten offen sein.
pause
