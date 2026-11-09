@echo off
rem Runs Flipper Builder from source (needs Python 3.10+). End users: use the .exe from Releases.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run: setting up Python environment and ufbt...
  py -3 -m venv .venv || python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip ufbt pywebview pyserial
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0flipper_builder.py"
