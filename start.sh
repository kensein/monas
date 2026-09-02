#!/bin/bash
set -e
cd "$(dirname "$0")"
export PYTHONPATH=.

# Load .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

pip install -q -r requirements.txt 2>/dev/null || true

echo "Starting API on ${API_HOST:-127.0.0.1}:${API_PORT:-8013}..."
python3 -m uvicorn backend.main:app --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8013}" &
API_PID=$!

echo "Starting Frontend on ${FRONTEND_HOST:-127.0.0.1}:${FRONTEND_PORT:-3013}..."
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}" FRONTEND_PORT="${FRONTEND_PORT:-3013}" BASE_PATH="${BASE_PATH:-}" python3 serve_frontend.py &
FE_PID=$!

echo ""
echo "Dashboard: http://localhost:${FRONTEND_PORT:-3013}"
echo "API:       http://localhost:${API_PORT:-8013}"
echo ""

trap "kill $API_PID $FE_PID 2>/dev/null" EXIT
wait
