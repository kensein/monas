#!/bin/bash
# Sync artifact ringan dari PC/HPC → webpsi, lalu import ke SQLite serve.
#
# Di webpsi (cron / setelah SCP):
#   scripts/sync_artifacts_to_webpsi.sh /path/to/artifacts/latest
#
# Atau dari PC (setelah daily_verify_pc):
#   scp -r data/artifacts/latest user@webpsi:/var/www/monas/data/artifacts/
#   ssh user@webpsi 'cd /var/www/monas && ./scripts/sync_artifacts_to_webpsi.sh'

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

SRC="${1:-$PROJECT_DIR/data/artifacts/latest}"
LOG_FILE="${PROJECT_DIR}/logs/artifact_sync.log"
mkdir -p "$(dirname "$LOG_FILE")" data/artifacts

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

if [ ! -d "$SRC" ] && [ ! -f "$SRC" ]; then
  log "ERROR: source tidak ada: $SRC"
  exit 1
fi

log "Import artifacts dari $SRC"
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/import_artifacts.py" --from "$SRC"
log "Selesai — dashboard siap serve readonly"
