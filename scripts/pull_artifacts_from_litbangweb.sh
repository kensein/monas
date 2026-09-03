#!/usr/bin/env bash
# Pola PSIIDN: webpsi (atau PC) PULL artifact dari litbangweb via SFTP :3346.
# litbangweb biasanya TIDAK bisa push ke webpsi:22 (timeout / beda jaringan).
#
# Di webpsi (setelah setup /var/www/monas):
#   export SFTP_PASSWORD=...   # atau pakai ssh-key / sshpass
#   ./scripts/pull_artifacts_from_litbangweb.sh
#
# Via PC (hub) bila webpsi juga belum bisa reach litbangweb:
#   # 1) PC download dari litbangweb
#   scp -P 3346 -r litbangweb@202.90.199.54:/opt/lampp/htdocs/wrf/monas_export/* D:\monas-artifacts\
#   # 2) PC upload ke webpsi
#   scp -r D:\monas-artifacts\* USER@WEBPSI:/var/www/monas/data/artifacts/
set -euo pipefail

SFTP_HOST="${SFTP_HOST:-202.90.199.54}"
SFTP_PORT="${SFTP_PORT:-3346}"
SFTP_USER="${SFTP_USER:-litbangweb}"
# Folder export di litbangweb (staging setelah compute; pola psiidn_export)
REMOTE_EXPORT="${REMOTE_EXPORT:-/opt/lampp/htdocs/wrf/monas_export}"
# Fallback: store compute langsung
REMOTE_STORE="${REMOTE_STORE:-/opt/lampp/htdocs/monas/compute-data/artifacts}"
LOCAL_DIR="${LOCAL_DIR:-/var/www/monas/data/artifacts}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mkdir -p "$LOCAL_DIR"

RSYNC_SSH="ssh -p ${SFTP_PORT} -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
remote_base=""

# Coba monas_export dulu (ada manifest), lalu store compute
for cand in "$REMOTE_EXPORT" "$REMOTE_STORE"; do
  if ssh -p "$SFTP_PORT" -o ConnectTimeout=15 "${SFTP_USER}@${SFTP_HOST}" \
      "test -f ${cand}/manifest.json" 2>/dev/null; then
    remote_base="$cand"
    break
  fi
done

if [ -z "$remote_base" ]; then
  log "ERROR: tidak menemukan manifest.json di litbangweb"
  log "  coba: $REMOTE_EXPORT  atau  $REMOTE_STORE"
  log "  pastikan compute sudah jalan + staging monas_export (lihat litbangweb_daily_compute.sh)"
  exit 1
fi

log "Pull ${SFTP_USER}@${SFTP_HOST}:${SFTP_PORT}${remote_base} → $LOCAL_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -az --delete -e "$RSYNC_SSH" \
    "${SFTP_USER}@${SFTP_HOST}:${remote_base}/manifest.json" \
    "${SFTP_USER}@${SFTP_HOST}:${remote_base}/runs" \
    "${SFTP_USER}@${SFTP_HOST}:${remote_base}/obs" \
    "${LOCAL_DIR}/"
else
  scp -P "$SFTP_PORT" -r \
    "${SFTP_USER}@${SFTP_HOST}:${remote_base}/manifest.json" \
    "${SFTP_USER}@${SFTP_HOST}:${remote_base}/runs" \
    "${SFTP_USER}@${SFTP_HOST}:${remote_base}/obs" \
    "${LOCAL_DIR}/"
fi

if [ ! -f "$LOCAL_DIR/manifest.json" ]; then
  log "ERROR: pull gagal — manifest.json belum ada di $LOCAL_DIR"
  exit 1
fi

log "OK: $(du -sh "$LOCAL_DIR" 2>/dev/null | cut -f1) · $(python3 -c "import json; m=json.load(open('$LOCAL_DIR/manifest.json')); print(len(m.get('runs',[])),'runs')" 2>/dev/null || echo '?')"
log "API f32 baca otomatis (tanpa import). Cek: curl -s http://127.0.0.1:8013/api/health"
