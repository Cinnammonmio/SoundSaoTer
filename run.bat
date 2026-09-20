@echo off
cd /d "%~dp0"
python soundboard.py
if errorlevel 1 pause
