@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Logicoin CUDA Worker CORE v0.12.15.3

echo ============================================================
echo Logicoin CUDA Worker CORE v0.12.15.3
echo ============================================================
echo.
echo Dieser Core-Build ist fuer x64 Native Tools Command Prompt gedacht.
echo CUDA 13 Fix: sm_61 / GTX 1050 Ti wird nicht kompiliert.
echo Aktive Targets: sm_75 RTX 20xx, sm_86 RTX 30xx.
echo.

where nvcc
if errorlevel 1 (
    echo FEHLER: nvcc fehlt.
    pause
    exit /b 1
)

where cl
if errorlevel 1 (
    echo FEHLER: cl.exe fehlt.
    pause
    exit /b 2
)

nvcc -O3 ^
  -gencode arch=compute_75,code=sm_75 ^
  -gencode arch=compute_86,code=sm_86 ^
  -gencode arch=compute_86,code=compute_86 ^
  logicoin_cuda_worker.cu -o logicoin_cuda_worker.exe

if errorlevel 1 (
    echo FEHLER: Build fehlgeschlagen.
    pause
    exit /b 3
)

echo.
echo Teste Streaming-CUDA-Worker ...
python logicoin_cuda_worker_protocol_test.py
if errorlevel 1 (
    echo.
    echo FEHLER: Protokolltest fehlgeschlagen.
    echo Der Worker wird NICHT nach dist kopiert.
    pause
    exit /b 4
)

if exist dist (
    copy /Y logicoin_cuda_worker.exe dist\logicoin_cuda_worker.exe
    echo Getesteter Worker nach dist kopiert.
)

echo.
echo FERTIG: logicoin_cuda_worker.exe erstellt und getestet.
pause
