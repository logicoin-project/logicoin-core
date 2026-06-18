@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Logicoin LOGIC Node v0.12.15.3

echo ============================================================
echo Logicoin LOGIC Node v0.12.15.3
echo ============================================================
echo.
echo Arbeitsordner:
echo %CD%
echo.
echo Befehl:
echo python logicoin_node.py
echo.
echo [%date% %time%] START start_logicoin_node.bat >> logicoin_batch_debug.log
echo Arbeitsordner: %CD% >> logicoin_batch_debug.log
echo Befehl: python logicoin_node.py >> logicoin_batch_debug.log
echo ------------------------------------------------------------
echo.

python logicoin_node.py
set "ERR=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
echo Beendet mit Fehlercode: %ERR%
echo [%date% %time%] ENDE start_logicoin_node.bat Fehlercode %ERR% >> logicoin_batch_debug.log
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
