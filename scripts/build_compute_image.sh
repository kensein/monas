#!/usr/bin/env bash
# Build image monas-compute di PC/laptop YANG PUNYA INTERNET.
# Lalu simpan tar untuk di-copy ke litbangweb (offline).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${IMAGE:-monas-compute:latest}"
OUT="${OUT:-$ROOT/dist/monas-compute.tar.gz}"

mkdir -p "$(dirname "$OUT")"
echo "Building $IMAGE ..."
docker build -f docker/compute/Dockerfile -t "$IMAGE" .

echo "Saving → $OUT"
docker save "$IMAGE" | gzip > "$OUT"
ls -lh "$OUT"
echo "Copy ke litbangweb, contoh:"
echo "  scp -P 3346 $OUT litbangweb@202.90.199.54:/home/litbangweb/"
echo "Di litbangweb: gunzip -c monas-compute.tar.gz | docker load"
