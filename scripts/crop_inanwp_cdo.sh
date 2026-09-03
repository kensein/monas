#!/usr/bin/env bash
# Crop InaNWP wrfout (~12GB, banyak 3D) → file 2D untuk HARP.
# Jalan di HOST litbangweb (CDO/ncks sudah ada). Tidak butuh Docker.
#
# Cron (sebelum compute):
#   0 4 * * * /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
#
# Output: /opt/lampp/htdocs/wrf/monas_nc/<nama-asli>.nc
# Compute Docker mount folder itu, bukan wrfout penuh.
set -euo pipefail

SRC_DIR="${SRC_DIR:-/opt/lampp/htdocs/wrf/wrfout}"
DST_DIR="${DST_DIR:-/opt/lampp/htdocs/wrf/monas_nc}"
LOG_DIR="${LOG_DIR:-/opt/lampp/htdocs/monas/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/cdo_crop.log}"
FORCE="${FORCE:-0}"
# Pola file InaNWP di litbangweb: 2026070112-d01-asim.nc
GLOB_PATTERNS="${GLOB_PATTERNS:-*-asim.nc wrfout_d01_*}"

mkdir -p "$DST_DIR" "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# stdout+stderr → file log (dan tetap ke terminal/cron parent)
exec > >(tee -a "$LOG_FILE") 2>&1

# Variabel 2D WRF / CF yang dipakai HARP (VERIFY_PARAMETERS + angin U/V + rain conv+nc).
# Jangan ambil 3D (U,V,W,T,P,PH,QVAPOR,QCLOUD,CLDFRA level, dll) — itu sumber 12GB.
HARP_VARS="
XLAT XLONG XTIME Times HGT
T2 T2M TH2 T2MAX T2MIN TSK
Q2 RH2 RH Td2 Td2m d2m TD2
PSFC MSLP slp
U10 V10 WS10 WD10
RAINC RAINNC RAINSH precip tp apcp
TCDC tcc CLDTOT
VIS
"

have_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! have_cmd cdo && ! have_cmd ncks; then
  log "ERROR: butuh cdo atau ncks (NCO) di PATH"
  exit 1
fi

# Nama var yang ada di file (ncdump), skip dimensi 3D WRF.
list_file_vars() {
  local f="$1"
  if ! have_cmd ncdump; then
    if have_cmd cdo; then
      cdo -s showname "$f" 2>/dev/null | tr ' ' '\n' | grep -E '^[A-Za-z_]' || true
    fi
    return
  fi
  ncdump -h "$f" 2>/dev/null | awk '
    $1 ~ /^(float|double|int|short|byte|char|uint|int64|uint64)/ {
      n=$2
      sub(/\(.*$/, "", n)
      gsub(/;/, "", n)
      line=$0
      if (line ~ /bottom_top|soil_layers|west_east_stag|south_north_stag/) next
      if (n ~ /^(U|V|W|T|P|PH|PHB|QVAPOR|QCLOUD|QRAIN|QSNOW|QGRAUP|QICE|CLDFRA|PB)$/) next
      print n
    }
  '
}

intersect_wanted() {
  local f="$1"
  local present wanted out=""
  present=$(list_file_vars "$f" | tr ' ' '\n' | sort -u)
  wanted=$(echo "$HARP_VARS" | tr ' ' '\n' | grep -E '^[A-Za-z]' | sort -u)
  out=$(comm -12 <(echo "$present") <(echo "$wanted") | tr '\n' ',' | sed 's/,$//')
  echo "$out"
}

need_crop() {
  local src="$1" dest="$2"
  [ "$FORCE" = "1" ] && return 0
  [ ! -f "$dest" ] && return 0
  [ "$src" -nt "$dest" ] && return 0
  local ss ds
  ss=$(stat -c%s "$src" 2>/dev/null || echo 0)
  ds=$(stat -c%s "$dest" 2>/dev/null || echo 0)
  [ "$ds" -lt 100000 ] && return 0
  # Dest hampir sebesar src = crop gagal / file penuh tertyalin
  if [ "$ss" -gt 0 ] && [ "$ds" -gt $((ss * 8 / 10)) ]; then
    return 0
  fi
  return 1
}

crop_one() {
  local src="$1"
  local dest="$DST_DIR/$(basename "$src")"
  # WRF kadang nama berisi ':' — tetap pakai basename asli
  dest="${dest%:}"

  if ! need_crop "$src" "$dest"; then
    local ds ss
    ss=$(stat -c%s "$src" 2>/dev/null || echo 0)
    ds=$(stat -c%s "$dest" 2>/dev/null || echo 0)
    log "SKIP $(basename "$src") (sudah crop $(awk -v d="$ds" -v s="$ss" 'BEGIN{printf "%.0f MB, src %.2f GB", d/1e6, s/1e9}'))"
    return 0
  fi

  local vars
  vars=$(intersect_wanted "$src")
  if [ -z "$vars" ]; then
    log "WARN $(basename "$src"): tidak ketemu var HARP — coba selname default T2,U10,V10,PSFC,RAINC,RAINNC,Q2,HGT,XLAT,XLONG"
    vars="T2,U10,V10,PSFC,RAINC,RAINNC,Q2,HGT,XLAT,XLONG,XTIME"
  fi

  local tmp="${dest}.tmp.$$"
  rm -f "$tmp"
  log "CROP $(basename "$src") → $(basename "$dest")  vars=$vars"

  local ok=0
  # ncks lebih ramah WRF (bukan CF). CDO sering gagal di wrfout.
  if have_cmd ncks; then
    if ncks -O -v "$vars" "$src" "$tmp" 2>/tmp/monas_ncks.err; then
      ok=1
    else
      log "ncks gagal: $(tr '\n' ' ' </tmp/monas_ncks.err | head -c 300)"
    fi
  fi

  if [ "$ok" != "1" ] && have_cmd cdo; then
    # CDO: drop nama yang tidak ada (selname gagal kalau ada yang missing)
    if cdo -s -f nc selname,"$vars" "$src" "$tmp" 2>/tmp/monas_cdo.err; then
      ok=1
    else
      log "cdo selname gagal: $(tr '\n' ' ' </tmp/monas_cdo.err | head -c 400)"
      # Fallback: coba satu-satu, merge
      local merged="" first=1
      IFS=',' read -r -a arr <<< "$vars"
      local piece="${tmp}.p"
      rm -f "$piece" "${tmp}.acc"
      for v in "${arr[@]}"; do
        [ -z "$v" ] && continue
        if cdo -s -f nc selname,"$v" "$src" "$piece" 2>/dev/null; then
          if [ "$first" = 1 ]; then
            mv "$piece" "${tmp}.acc"
            first=0
          else
            cdo -s merge "${tmp}.acc" "$piece" "${tmp}.m" 2>/dev/null && mv "${tmp}.m" "${tmp}.acc"
            rm -f "$piece"
          fi
        fi
      done
      if [ -f "${tmp}.acc" ]; then
        mv "${tmp}.acc" "$tmp"
        ok=1
      fi
      rm -f "$piece" "${tmp}.m" "${tmp}.acc"
    fi
  fi

  if [ "$ok" != "1" ] || [ ! -s "$tmp" ]; then
    log "ERROR crop gagal: $src"
    rm -f "$tmp"
    return 1
  fi

  mv -f "$tmp" "$dest"
  local ss ds
  ss=$(stat -c%s "$src")
  ds=$(stat -c%s "$dest")
  log "OK $(basename "$dest")  $(awk -v s="$ss" -v d="$ds" 'BEGIN{printf "%.2f GB → %.0f MB (%.1f%%)", s/1e9, d/1e6, 100*d/s}')"
}

log "=== crop InaNWP HARP  SRC=$SRC_DIR  DST=$DST_DIR  FORCE=$FORCE ==="

if [ ! -d "$SRC_DIR" ]; then
  log "ERROR: SRC_DIR tidak ada: $SRC_DIR"
  exit 1
fi

shopt -s nullglob
count=0
fail=0
for pat in $GLOB_PATTERNS; do
  for f in "$SRC_DIR"/$pat; do
    [ -f "$f" ] || continue
    case "$f" in
      *.tmp|*.tmp.*) continue ;;
    esac
    count=$((count + 1))
    if ! crop_one "$f"; then
      fail=$((fail + 1))
    fi
  done
done

n_out=0
for f in "$DST_DIR"/*.nc; do
  [ -f "$f" ] && n_out=$((n_out + 1))
done
log "Selesai: diproses=$count gagal=$fail file_di_dst=$n_out"
if [ "$count" -eq 0 ]; then
  log "WARN: tidak ada file cocok di $SRC_DIR (pola: $GLOB_PATTERNS)"
  exit 1
fi
if [ "$fail" -gt 0 ]; then
  exit 1
fi
exit 0
