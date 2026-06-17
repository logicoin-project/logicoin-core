@echo off
echo.
echo Configure a neutral Git author before your first commit.
echo Use the GitHub noreply address shown in GitHub Settings ^> Emails.
echo.
set /p GITNAME=Neutral Git name [Logicoin Project]: 
if "%GITNAME%"=="" set GITNAME=Logicoin Project
set /p GITEMAIL=GitHub noreply email: 
if "%GITEMAIL%"=="" (
  echo No email entered. Nothing changed.
  pause
  exit /b 1
)
git config user.name "Logicoin Project"
git config user.email "LogicoinDev@users.noreply.github.com"
echo.
git config user.name
git config user.email
echo.
pause
