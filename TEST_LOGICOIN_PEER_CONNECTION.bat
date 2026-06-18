@echo off
setlocal
title Logicoin Peer Verbindungstest v0.12.15.3

echo Beispiel: 192.168.0.34
set /p PEER_IP=IP-Adresse des anderen PCs: 

echo.
echo Teste TCP-Port 8080...
powershell -NoProfile -Command "Test-NetConnection -ComputerName '%PEER_IP%' -Port 8080 | Format-List ComputerName,RemoteAddress,RemotePort,InterfaceAlias,SourceAddress,TcpTestSucceeded"

echo.
echo Teste Logicoin /info...
powershell -NoProfile -Command "try { Invoke-RestMethod 'http://%PEER_IP%:8080/info' -TimeoutSec 5 | ConvertTo-Json -Depth 5 } catch { Write-Host $_.Exception.Message -ForegroundColor Red }"

echo.
pause
