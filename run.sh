#!/bin/bash
# Chapter Watermarker - local launcher (macOS/Linux).
# Creates the virtualenv, installs dependencies, then starts the app and opens
# your browser. Outputs are saved locally (charcoal/ and watermarked/ folders).
#
# Usage:  bash run.sh        (or double-click run.command on macOS)
# Optional: put GEMINI_API_KEY / LOCAL_SAVE_DIR in a local.env file next to this.

set -e
cd "$(dirname "$0")"

# Load optional local settings (API keys, output dir). Never committed (*.env).
if [ -f local.env ]; then
  set -a; . ./local.env; set +a
fi

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.11+ and retry." >&2
  exit 1
fi

if [ ! -d venv ]; then
  echo "Creating virtual environment (first run)..."
  "$PY" -m venv venv
fi

echo "Installing/updating dependencies..."
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt

# Save results on this machine by default (under ~/Downloads): the charcoal tool
# writes to <dir>/charcoal and the watermarker writes to <dir>/watermarked.
export LOCAL_SAVE_DIR="${LOCAL_SAVE_DIR:-$HOME/Downloads}"

# Heads-up about the optional PDF-export dependency.
if ! command -v soffice >/dev/null 2>&1 && ! command -v libreoffice >/dev/null 2>&1; then
  echo "NOTE: LibreOffice not found - DOCX->PDF export will be unavailable (everything else works)."
fi

echo "Starting Chapter Watermarker (a browser tab will open)..."
exec venv/bin/python app.py
