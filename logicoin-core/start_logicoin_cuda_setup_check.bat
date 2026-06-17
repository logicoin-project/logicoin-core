@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Logicoin LOGIC CUDA Setup Check v0.12.15.3

echo ============================================================
echo Logicoin LOGIC CUDA Setup Check v0.12.15.3
echo ============================================================
echo.
echo Arbeitsordner:
echo %CD%
echo.
echo Befehl:
echo python logicoin_cuda_setup_check.py
echo.
echo [%date% %time%] START start_logicoin_cuda_setup_check.bat >> logicoin_batch_debug.log
echo Arbeitsordner: %CD% >> logicoin_batch_debug.log
echo Befehl: python logicoin_cuda_setup_check.py >> logicoin_batch_debug.log
echo ------------------------------------------------------------
echo.

python logicoin_cuda_setup_check.py
set "ERR=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
echo Beendet mit Fehlercode: %ERR%
echo [%date% %time%] ENDE start_logicoin_cuda_setup_check.bat Fehlercode %ERR% >> logicoin_batch_debug.log
echo.

if not "%ERR%"=="0" (
    echo FEHLER erkannt.
    echo Bitte Screenshot von diesem Fenster schicken.
    echo Logdatei: logicoin_batch_debug.log
) else (
    echo Fertig ohne Fehlercode.
)

echo.
pause
