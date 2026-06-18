@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Logicoin Builder v0.12.15.3
color 0A

echo ============================================================
echo Logicoin / LOGIC Builder v0.12.15.3 - LAN Testnet
echo ============================================================
echo.
echo Builds:
echo - LogicoinControlCenter.exe
echo - LogicoinNode.exe
echo - LogicoinCpuMiner.exe
echo - LogicoinGpuMiner.exe
echo - LogicoinCudaSetupCheck.exe
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo FEHLER: Python wurde nicht gefunden.
    pause
    exit /b 1
)

python -m pip install --upgrade pyinstaller

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
for %%F in (*.spec) do del /q "%%F"

echo.
echo [1/5] Control Center...
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name LogicoinControlCenter ^
  --hidden-import logicoin_core ^
  --hidden-import logicoin_node ^
  --hidden-import logicoin_peer_network ^
  --hidden-import logicoin_headless_miner ^
  --hidden-import logicoin_gpu_miner ^
  --hidden-import logicoin_config_editor ^
  --hidden-import logicoin_public_network ^
  --hidden-import logicoin_release_tools ^
  --add-data "logicoin_public_network.json;." ^
  logicoin_control_center.py
if errorlevel 1 goto BUILD_ERROR

echo.
echo [2/5] Standalone Node...
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --console ^
  --name LogicoinNode ^
  --hidden-import logicoin_core ^
  --hidden-import logicoin_peer_network ^
  --hidden-import logicoin_public_network ^
  --add-data "logicoin_public_network.json;." ^
  logicoin_node.py
if errorlevel 1 goto BUILD_ERROR

echo.
echo [3/5] CPU Miner...
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --console ^
  --name LogicoinCpuMiner ^
  --hidden-import logicoin_core ^
  logicoin_headless_miner.py
if errorlevel 1 goto BUILD_ERROR

echo.
echo [4/5] GPU Miner...
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --console ^
  --name LogicoinGpuMiner ^
  --hidden-import logicoin_core ^
  logicoin_gpu_miner.py
if errorlevel 1 goto BUILD_ERROR

echo.
echo [5/5] CUDA Setup Check...
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --console ^
  --name LogicoinCudaSetupCheck ^
  logicoin_cuda_setup_check.py
if errorlevel 1 goto BUILD_ERROR

echo.
echo Kopiere Release-Dateien nach dist...
copy /Y logicoin_config.json dist\logicoin_config.json >nul
copy /Y logicoin_public_network.json dist\logicoin_public_network.json >nul
copy /Y logicoin_peers.json dist\logicoin_peers.json >nul
copy /Y logicoin_miner_profiles.json dist\logicoin_miner_profiles.json >nul
copy /Y LAN_TESTNET_SCHRITTE.txt dist\LAN_TESTNET_SCHRITTE.txt >nul
copy /Y FIX_LOGICOIN_FIREWALL_ADMIN.bat dist\FIX_LOGICOIN_FIREWALL_ADMIN.bat >nul
copy /Y TEST_LOGICOIN_PEER_CONNECTION.bat dist\TEST_LOGICOIN_PEER_CONNECTION.bat >nul
copy /Y logicoin_release.json dist\logicoin_release.json >nul
copy /Y TEST_RELEASE_READINESS.txt dist\TEST_RELEASE_READINESS.txt >nul
copy /Y PAYOUT_CONCEPT.txt dist\PAYOUT_CONCEPT.txt >nul

if exist logicoin_cuda_worker.exe (
    copy /Y logicoin_cuda_worker.exe dist\logicoin_cuda_worker.exe >nul
    echo CUDA Worker automatisch nach dist kopiert.
) else (
    echo HINWEIS: logicoin_cuda_worker.exe fehlt.
    echo Fuer GPU-Mining und GPU-Benchmark danach BUILD_CUDA_WORKER_SAFE.bat ausfuehren.
)

echo.
echo ============================================================
echo FERTIG
echo ============================================================
echo dist\LogicoinControlCenter.exe
echo dist\LogicoinNode.exe
echo dist\LogicoinCpuMiner.exe
echo dist\LogicoinGpuMiner.exe
echo dist\LogicoinCudaSetupCheck.exe
echo.
echo.
echo Verwalteter Node ist in LogicoinControlCenter.exe eingebettet.
echo Dadurch bleibt der Node immer auf derselben Version.
echo.
echo CUDA Worker:
echo - vorhanden: automatisch nach dist kopiert
echo - fehlend: BUILD_CUDA_WORKER_SAFE.bat starten
echo.
pause
exit /b 0

:BUILD_ERROR
echo.
echo BUILD FEHLGESCHLAGEN.
pause
exit /b 1
