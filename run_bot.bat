@echo off
echo ==================================================
echo 🎬 Theater Bot — Windows Launcher (Anaconda)
echo ==================================================
echo.

:: Detect Anaconda Python
set "PY_PATH=C:\Users\homep\anaconda3\python.exe"

if not exist "%PY_PATH%" (
    echo ❌ Anaconda Python not found at %PY_PATH%
    echo Please install Anaconda/Miniconda or edit this batch file with your Python path.
    pause
    exit /b 1
)

echo 📦 Using Python from Anaconda: %PY_PATH%
echo 🚀 Starting Theater Bot...
echo.
"%PY_PATH%" theater_automation.py
pause
