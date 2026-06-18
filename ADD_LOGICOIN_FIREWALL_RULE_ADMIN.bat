@echo off
title Logicoin Firewall TCP 8080

net session >nul 2>&1
if errorlevel 1 (
    echo Administratorrechte werden angefordert...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

netsh advfirewall firewall add rule name="Logicoin LAN Node TCP 8080" dir=in action=allow protocol=TCP localport=8080 profile=private
echo.
echo Firewall-Regel für TCP 8080 wurde angelegt oder war bereits vorhanden.
pause
