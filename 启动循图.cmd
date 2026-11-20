@echo off
cd /d "%~dp0"
if exist "dist\RouteOverlay\RouteOverlay.exe" (
    start "" "dist\RouteOverlay\RouteOverlay.exe"
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run.py"
    exit /b
)
echo Please build or install dependencies first. See README.md.
pause
