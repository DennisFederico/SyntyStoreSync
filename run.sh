#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "Starting SyntyStoreSync on http://127.0.0.1:8765 ..."

if command -v open >/dev/null 2>&1; then
  (sleep 1 && open "http://127.0.0.1:8765") &
elif command -v xdg-open >/dev/null 2>&1; then
  (sleep 1 && xdg-open "http://127.0.0.1:8765") &
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload

