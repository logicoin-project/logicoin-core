@echo off
setlocal
cd /d "%~dp0"
title Logicoin Control Center v0.12.15.3

echo Pruefe alte Logicoin-Nodes...
taskkill /F /T /IM LogicoinNode.exe >nul 2>&1

if exist dist\LogicoinControlCenter.exe (
    start "" dist\LogicoinControlCenter.exe
) else if exist LogicoinControlCenter.exe (
    start "" LogicoinControlCenter.exe
) else (
    python logicoin_control_center.py
)
