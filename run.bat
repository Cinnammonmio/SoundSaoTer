@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" soundboard.py
) else (
    python soundboard.py
)
if errorlevel 1 pause
