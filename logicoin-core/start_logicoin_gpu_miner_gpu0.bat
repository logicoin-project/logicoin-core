@echo off
title Logicoin LOGIC GPU Miner GPU0 v0.12.15.3
cd /d "%~dp0"
if exist LogicoinGpuMiner.exe (
    LogicoinGpuMiner.exe --backend auto --device 0 --node-url http://127.0.0.1:8080 --miner-address logic1_public_test_wallet --stats-file logicoin_gpu_miner_stats_gpu0.json
) else (
    python logicoin_gpu_miner.py --backend auto --device 0 --node-url http://127.0.0.1:8080 --miner-address logic1_public_test_wallet --stats-file logicoin_gpu_miner_stats_gpu0.json
)
pause
