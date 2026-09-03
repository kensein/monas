#!/usr/bin/env bash
# Opsional: dari PC Linux / Git Bash — fetch obs + sync SFTP (paramiko via Python).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.
mkdir -p logs
echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily_obs_pc" | tee -a logs/daily_obs_pc.log
python scripts/fetch_obs_local.py --days 3 --monthly --sync
echo "OK"
