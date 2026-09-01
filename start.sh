#!/bin/bash
set -e
cd "$(dirname "$0")"
export PYTHONPATH=.
export SFTP_PASSWORD="${SFTP_PASSWORD:-_Pusl1tb4ng.123_}"

echo "Starting API on :8013..."
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8013 &
API_PID=$!

echo "Starting Frontend on :3013..."
python3 serve_frontend.py &
FE_PID=$!

trap "kill $API_PID $FE_PID 2>/dev/null" EXIT
wait
