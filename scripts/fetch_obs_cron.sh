#!/bin/bash
# Cron: fetch observasi BMKG Sinoptik (10 hari terakhir)
# 0 */4 * * * /var/www/monas/scripts/fetch_obs_cron.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
API_PORT="${API_PORT:-8013}"

curl -sf -X POST "http://127.0.0.1:${API_PORT}/api/obs/sync-recent?days=10" \
  | tee -a "${PROJECT_DIR}/logs/obs_sync.log"
echo "" >> "${PROJECT_DIR}/logs/obs_sync.log"
