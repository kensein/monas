#!/usr/bin/env bash
# Setup sekali di litbangweb (setelah monas-compute.tar(.gz) di-copy ke server).
# Jalankan sebagai user yang bisa docker (atau sudo).
set -euo pipefail

MONAS_ROOT="${MONAS_ROOT:-/opt/lampp/htdocs/monas}"
IMAGE_TAR="${1:-/home/litbangweb/monas-compute.tar.gz}"
DATA_DIR="${DATA_DIR:-$MONAS_ROOT/compute-data}"
ENV_FILE="${ENV_FILE:-$MONAS_ROOT/compute.env}"

echo "=== Install MONAS compute @ litbangweb ==="
mkdir -p "$MONAS_ROOT/scripts" "$MONAS_ROOT/logs" "$DATA_DIR"

if [ -f "$IMAGE_TAR" ]; then
  echo "Loading image: $IMAGE_TAR"
  case "$IMAGE_TAR" in
    *.gz) gunzip -c "$IMAGE_TAR" | docker load ;;
    *) docker load -i "$IMAGE_TAR" ;;
  esac
else
  echo "WARN: $IMAGE_TAR tidak ada — pastikan docker images | grep monas-compute"
fi

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$(dirname "$0")/../docker/compute/env.litbangweb.example" ]; then
    cp "$(dirname "$0")/../docker/compute/env.litbangweb.example" "$ENV_FILE"
  else
    cat > "$ENV_FILE" << 'EOF'
OFFLINE_OBS_MODE=true
USE_LOCAL_OBS_JSON=true
USE_DUMMY_MODELS=false
SEED_DEMO_DATA=false
DISABLE_SFTP=true
PARALLEL_VERIFY=true
PARALLEL_BACKEND=process
PARALLEL_WORKERS=4
EOF
  fi
  echo "Created $ENV_FILE — edit WEBPSI_* jika sync otomatis ke webpsi"
fi

# Salin script runner + crop CDO
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /opt/lampp/htdocs/wrf/monas_nc
for s in litbangweb_daily_compute.sh crop_inanwp_cdo.sh; do
  if [ -f "$SCRIPT_DIR/$s" ]; then
    cp -f "$SCRIPT_DIR/$s" "$MONAS_ROOT/scripts/$s"
    chmod +x "$MONAS_ROOT/scripts/$s"
  fi
done

echo
echo "Uji crop (sekali, ~menit per file 12GB → puluhan–ratusan MB):"
echo "  $MONAS_ROOT/scripts/crop_inanwp_cdo.sh"
echo "  ls -lh /opt/lampp/htdocs/wrf/monas_nc/"
echo
echo "Uji compute (otomatis crop jika belum):"
echo "  $MONAS_ROOT/scripts/litbangweb_daily_compute.sh"
echo
echo "Cron (obs PC ~02:00; crop 04:00; HARP 04:30):"
echo "  0 4 * * * $MONAS_ROOT/scripts/crop_inanwp_cdo.sh"
echo "  30 4 * * * $MONAS_ROOT/scripts/litbangweb_daily_compute.sh >> $MONAS_ROOT/logs/compute.log 2>&1"
echo
docker images | grep monas-compute || true
