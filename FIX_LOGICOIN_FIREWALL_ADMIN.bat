@echo off
setlocal
title Logicoin Firewall Reparatur v0.12.15.3

net session >nul 2>&1
if errorlevel 1 (
    echo Administratorrechte werden angefordert...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================================
echo Logicoin Firewall Reparatur - TCP 8080
echo ============================================================
echo.

netsh advfirewall firewall delete rule name="Logicoin LAN Node TCP 8080" >nul 2>&1
netsh advfirewall firewall delete rule name="Logicoin LAN Node TCP 8080 IN" >nul 2>&1
netsh advfirewall firewall delete rule name="Logicoin LAN Node TCP 8080 OUT" >nul 2>&1

netsh advfirewall firewall add rule name="Logicoin LAN Node TCP 8080 IN" dir=in action=allow protocol=TCP localport=8080 profile=any
netsh advfirewall firewall add rule name="Logicoin LAN Node TCP 8080 OUT" dir=out action=allow protocol=TCP remoteport=8080 profile=any

echo.
echo Aktuelles Netzwerkprofil:
powershell -NoProfile -Command "Get-NetConnectionProfile | Format-Table -AutoSize Name,InterfaceAlias,NetworkCategory,IPv4Connectivity"
echo.
echo Port 8080 LISTENING:
netstat -ano | findstr LISTENING | findstr :8080
echo.
echo Fertig.
pause
