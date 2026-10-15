@echo off
cd /d "%~dp0"
uv run uvicorn --port 8443 --reload openbachelors.app:app %*
pause
