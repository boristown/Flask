@echo off
echo Restarting FlaskService...
sc stop FlaskService
timeout /t 5 /nobreak >nul
sc start FlaskService
echo Status:
sc query FlaskService | findstr /I "STATE"
