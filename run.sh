#!/usr/bin/env bash
# One-command boot: venv -> deps -> migrations -> seed -> serve.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "[run] creating virtualenv..."
  python3 -m venv .venv
fi
echo "[run] installing dependencies..."
.venv/bin/pip install -q -r requirements.txt
echo "[run] seeding demo data..."
.venv/bin/python scripts/seed.py
echo "[run] starting API on ${APP_HOST:-127.0.0.1}:${APP_PORT:-8000} ..."
exec .venv/bin/python -m src.api.app
