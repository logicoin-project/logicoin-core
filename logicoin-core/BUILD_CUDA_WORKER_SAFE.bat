@echo off
cd /d "%~dp0"
start "Logicoin CUDA Worker SAFE Build v0.12.15.3" cmd /k "cd /d "%~dp0" && python logicoin_cuda_worker_builder.py"
