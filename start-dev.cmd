@echo off
setlocal
set ROOT=%~dp0
set FRONTEND_URL=http://127.0.0.1:5173/

echo [PM-Pal] Opening frontend dev server in a new window...
start "PM-Pal Frontend" cmd /k ""%ROOT%start-frontend-dev.cmd""

echo [PM-Pal] Waiting briefly before opening the browser...
timeout /t 5 /nobreak >nul
start "" "%FRONTEND_URL%"

echo [PM-Pal] Starting backend in this window. Keep this window open while using the app.
title PM-Pal Backend
call "%ROOT%start-backend-dev.cmd"
