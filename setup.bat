@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Screen Translator - Setup

rem PyPI direct is unusably slow from this network, use the Tsinghua mirror.
set MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

set PY311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
if not exist "%PY311%" set PY311=python

if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating virtual environment...
    "%PY311%" -m venv .venv
    if errorlevel 1 (
        echo [X] Failed to create venv. Install Python 3.11+ first.
        pause
        exit /b 1
    )
)

set VPY=.venv\Scripts\python.exe

echo [2/5] Installing GUI and networking dependencies...
"%VPY%" -m pip install --disable-pip-version-check -q -i %MIRROR% -r requirements.txt
if errorlevel 1 (
    echo [X] Dependency install failed. Check your network.
    pause
    exit /b 1
)

echo [3/5] Removing conflicting onnxruntime builds...
"%VPY%" -m pip uninstall -y onnxruntime onnxruntime-gpu onnxruntime-directml >nul 2>&1

echo [4/5] Installing OCR engine (rapidocr, models bundled, about 30MB)...
"%VPY%" -m pip install --disable-pip-version-check -q -i %MIRROR% "rapidocr>=3.9.0"
if errorlevel 1 (
    echo [X] OCR engine install failed.
    pause
    exit /b 1
)

rem DirectML lets OCR run on the GPU; CPU is the automatic fallback.
"%VPY%" -m pip install --disable-pip-version-check -q -i %MIRROR% "onnxruntime-directml==1.22.0" 2>nul
if errorlevel 1 (
    echo     NOTE: DirectML unavailable, OCR will run on CPU. Works, just slower.
)

echo [5/5] Running self check...
"%VPY%" tools\selfcheck.py
echo.
echo Done. From now on just double-click run.bat.
pause