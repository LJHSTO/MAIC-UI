@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-openmaic-local-batch.ps1" -BaseUrl http://localhost:3001 -CsvPath "%~dp0openmaic-courses.csv" -OutputDir "%~dp0outputs\openmaic" -SaveClassroomJson
echo.
pause
