@echo off
REM One-step launcher for Windows: creates a virtualenv on first run, then starts the app.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install -q -r requirements.txt
.venv\Scripts\python -m option_trader --open %*
