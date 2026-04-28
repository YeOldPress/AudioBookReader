@echo off
:: Heaven's River Reader — Windows launcher
:: Double-click this file to start the player in your browser.

cd /d "%~dp0"

where node >nul 2>&1
if %errorlevel%==0 (
    start "" node launch.js
    exit /b
)

echo Node.js is not installed or not in PATH.
echo Install it from https://nodejs.org/
pause
