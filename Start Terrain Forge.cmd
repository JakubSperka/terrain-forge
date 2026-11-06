@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Terrain Forge - GIS to 3D Print environment is missing. See README.md for setup instructions.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m terrain_forge.desktop
if errorlevel 1 pause
