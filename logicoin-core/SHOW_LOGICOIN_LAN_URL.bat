@echo off
title Logicoin LAN IP
echo ============================================================
echo Logicoin LAN-IP
echo ============================================================
ipconfig | findstr /i "IPv4"
echo.
echo Node-URL ist normalerweise:
echo http://DEINE-IP:8080
echo.
pause
