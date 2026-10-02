@echo off
REM Chapter Watermarker - local launcher (Windows). Double-click or run in a prompt.
cd /d "%~dp0"

if exist local.env (
  for /f "usebackq tokens=1,* delims==" %%A in ("local.env") do set "%%A=%%B"
)

if not exist venv (
  echo Creating virtual environment ^(first run^)...
  python -m venv venv
)

echo Installing/updating dependencies...
venv\Scripts\python -m pip install --quiet --upgrade pip
venv\Scripts\python -m pip install --quiet -r requirements.txt

if not defined LOCAL_SAVE_DIR set "LOCAL_SAVE_DIR=%USERPROFILE%\Downloads"

echo Starting Chapter Watermarker (a browser tab will open)...
venv\Scripts\python app.py
