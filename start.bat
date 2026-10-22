@echo off
chcp 65001 >nul 2>&1
title Requirement Kanban AI Assistant - MVP v1.0.0

echo.
echo ============================================================
echo   Requirement Kanban AI Assistant - MVP v1.0.0
echo ============================================================
echo.

REM ---- Step 1: Check Python ----
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.10 or later from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: During installation, check the box
    echo   "Add Python to PATH"
    echo at the bottom of the installer window.
    echo.
    pause
    exit /b 1
)
python --version

REM ---- Step 2: Create virtual environment ----
if not exist ".venv" (
    echo.
    echo [2/4] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo.
    echo [2/4] Virtual environment .venv already exists, skip.
)

REM ---- Step 3: Install dependencies ----
echo.
echo [3/4] Installing dependencies...
echo       First run takes 1-3 minutes. Please be patient.
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install dependencies.
    echo Try again with a domestic mirror:
    echo   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    pause
    exit /b 1
)

REM ---- Step 4: Start Streamlit ----
echo.
echo [4/4] Starting Streamlit...
echo.
echo ============================================================
echo   Browser will open at:  http://localhost:8508
echo   To stop:  press Ctrl+C  or  close this window
echo ============================================================
echo.
echo NOTE: Configure your LLM API Key in the left sidebar
echo       after the page loads.
echo.

streamlit run app.py --server.port 8508

pause
