@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0batch-generate-courses.ps1" -BaseUrl http://127.0.0.1:8000/api -DownloadHtml
echo.
pause
