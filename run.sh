#!/usr/bin/env bash
# One-step launcher for macOS/Linux: creates a virtualenv on first run, then starts the app.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
fi
./.venv/bin/pip install -q -r requirements.txt
exec ./.venv/bin/python -m option_trader --open "$@"
