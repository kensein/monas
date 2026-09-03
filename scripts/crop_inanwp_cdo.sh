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

# Jika di-copy dari Windows (CRLF), pola glob jadi *-asim.nc\r → 0 file
if command -v sed >/dev/null 2>&1 && grep -q $'\r' "$0" 2>/dev/null; then
  _clean="$(mktemp)"
  tr -d '\r' <"$0" >"$_clean"
  chmod +x "$_clean"
  exec /bin/bash "$_clean" "$@"
fi

SRC_DIR="${SRC_DIR:-/opt/lampp/htdocs/wrf/wrfout}"
DST_DIR="${DST_DIR:-/opt/lampp/htdocs/wrf/monas_nc}"
LOG_DIR="${LOG_DIR:-/opt/lampp/htdocs/monas/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/cdo_crop.log}"
FORCE="${FORCE:-0}"
# Rekursif di SRC_DIR. Nama InaNWP di litbangweb: 2026090200-d01-asim.nc
FIND_MAXDEPTH="${FIND_MAXDEPTH:-4}"
FIND_NAME="${FIND_NAME:-}"
GLOB_PATTERNS="${GLOB_PATTERNS:-}"

# Strip CR dari env (kalau di-export dari Windows)
SRC_DIR="${SRC_DIR//$'\r'/}"
DST_DIR="${DST_DIR//$'\r'/}"
LOG_DIR="${LOG_DIR//$'\r'/}"
LOG_FILE="${LOG_FILE//$'\r'/}"
FORCE="${FORCE//$'\r'/}"
FIND_MAXDEPTH="${FIND_MAXDEPTH//$'\r'/}"
FIND_NAME="${FIND_NAME//$'\r'/}"
GLOB_PATTERNS="${GLOB_PATTERNS//$'\r'/}"

mkdir -p "$DST_DIR" "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# stdout+stderr → file log (dan tetap ke terminal/cron parent)
exec > >(tee -a "$LOG_FILE") 2>&1

# Sinkronkan tee sebelum exit (hindari prompt muncul sebelum baris terakhir)
_finish() {
  sleep 0.2 2>/dev/null || true
}
trap _finish EXIT

list_candidate_nc() {
  # Cetak path absolut file NC yang mungkin InaNWP (satu path per baris)
  local depth=(-maxdepth "$FIND_MAXDEPTH")
  if [ -n "$FIND_NAME" ]; then
    find "$SRC_DIR" "${depth[@]}" -type f -name "$FIND_NAME" 2>/dev/null | sort -u
    return
  fi
  if [ -n "$GLOB_PATTERNS" ]; then
    local pat
    for pat in $GLOB_PATTERNS; do
      find "$SRC_DIR" "${depth[@]}" -type f -name "$pat" 2>/dev/null
    done | sort -u
    return
  fi
  # Default: asim + wrfout (bukan hanya *-asim.nc — kadang tanpa strip sebelum asim)
  {
    find "$SRC_DIR" "${depth[@]}" -type f \( \
      -name '*asim*.nc' -o -name '*asim*.NC' -o \
      -name 'wrfout_d01_*' -o -name 'wrfout_d01_*.nc' -o \
      -name '*-d01-*.nc' \
    \) 2>/dev/null
  } | sort -u
}

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
  local base
  base="$(basename "$src")"
  base="${base//$'\r'/}"
  local dest="$DST_DIR/$base"
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

mapfile -t FILES < <(list_candidate_nc || true)
count=0
fail=0
seen=""

if [ "${#FILES[@]}" -eq 0 ] || [ -z "${FILES[0]:-}" ]; then
  log "WARN: tidak ada file cocok di $SRC_DIR (rekursif maxdepth=$FIND_MAXDEPTH)"
  log "Isi level-1 (bantu debug):"
  ls -lah "$SRC_DIR" 2>/dev/null | head -40 | while read -r line; do log "  $line"; done
  log "Sample find *.nc (max 20):"
  find "$SRC_DIR" -maxdepth "$FIND_MAXDEPTH" -type f -name '*.nc' 2>/dev/null | head -20 | while read -r p; do
    log "  $p ($(stat -c%s "$p" 2>/dev/null || echo '?') bytes)"
  done
  log "Coba manual: FIND_NAME='*.nc' $0   atau   ls -lah $SRC_DIR"
  exit 1
fi

log "Ditemukan ${#FILES[@]} kandidat NC"
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in
    *.tmp|*.tmp.*) continue ;;
  esac
  # Dedup
  case " $seen " in
    *" $f "*) continue ;;
  esac
  seen="$seen $f"
  count=$((count + 1))
  if ! crop_one "$f"; then
    fail=$((fail + 1))
  fi
done

n_out=0
shopt -s nullglob
for f in "$DST_DIR"/*.nc; do
  [ -f "$f" ] && n_out=$((n_out + 1))
done
log "Selesai: diproses=$count gagal=$fail file_di_dst=$n_out"
if [ "$count" -eq 0 ]; then
  log "WARN: tidak ada file diproses"
  exit 1
fi
if [ "$fail" -gt 0 ]; then
  exit 1
fi
exit 0
