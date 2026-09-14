#!/usr/bin/env bash
# Starts everything Verdict needs to run locally.
#
# Ollama's own daemon (`ollama serve`) is treated as a separate,
# externally-managed service — this script checks it's reachable and tells
# you how to start it if not, rather than launching it itself, since how
# it's meant to run (systemd, a login script, manual) varies by machine and
# isn't this project's to manage.
#
# The dashboard (FastAPI + the static frontend) is this project's own
# service, so this script does own starting/stopping that.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
PORT="${PORT:-8731}"
HOST="${HOST:-127.0.0.1}"

echo "==> Checking Ollama at ${OLLAMA_URL} ..."
if curl -sf -m 3 "${OLLAMA_URL}/api/version" > /dev/null 2>&1; then
  echo "    Ollama is running."
else
  echo "    Ollama is not reachable at ${OLLAMA_URL}."
  echo "    Start it first, e.g.:  ollama serve &"
  echo "    (Verdict's chat and analysis features need it — the dashboard"
  echo "     itself will still start, but those features will fail.)"
fi

if [ ! -d ".venv" ]; then
  echo "==> No .venv found — creating one with uv ..."
  uv venv --python 3.12 .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -e ./vendor/TradingAgents
  uv pip install nautilus_trader fastapi "uvicorn[standard]" httpx
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

EXISTING_PID="$(lsof -ti tcp:"${PORT}" 2>/dev/null || true)"
if [ -n "${EXISTING_PID}" ]; then
  echo "==> Port ${PORT} is already in use (pid ${EXISTING_PID})."
  echo "    Assuming the dashboard is already running at http://${HOST}:${PORT}"
  echo "    Stop it first (kill ${EXISTING_PID}) if you want a fresh instance."
  exit 0
fi

echo "==> Starting the dashboard on http://${HOST}:${PORT} ..."
echo "    Press Ctrl+C to stop."
exec uvicorn webapp.server:app --host "${HOST}" --port "${PORT}"
