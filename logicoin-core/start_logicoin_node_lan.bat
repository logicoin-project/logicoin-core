@echo off
setlocal
cd /d "%~dp0"
title Logicoin LAN Node v0.12.15.3

if exist LogicoinNode.exe (
    LogicoinNode.exe --host 0.0.0.0 --port 8080
) else (
    python logicoin_node.py --host 0.0.0.0 --port 8080
)
pause
