@echo off
cd /d "%~dp0"
title Screen Translator

if not exist ".venv\Scripts\pythonw.exe" (
    echo [X] Virtual environment not found. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

rem pythonw = no console window
start "" ".venv\Scripts\pythonw.exe" "main.py"