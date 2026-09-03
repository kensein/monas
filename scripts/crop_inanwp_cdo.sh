#!/usr/bin/env bash
# Crop InaNWP NC (~12GB) → file tipis untuk HARP.
# litbangweb: file *-d01-asim.nc = GrADS/CF turunan (t2m,u10,…), BUKAN wrfout WRF mentah.
# Semua field punya dim lev=19 → wajib -d lev,0 (ambil permukaan) + selname var HARP.
#
# Cron:
#   0 4 * * * /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
# Output: /opt/lampp/htdocs/wrf/monas_nc/<nama>.nc
set -euo pipefail

# Jika di-copy dari Windows (CRLF), pola/path rusak
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
FIND_MAXDEPTH="${FIND_MAXDEPTH:-4}"
FIND_NAME="${FIND_NAME:-}"
GLOB_PATTERNS="${GLOB_PATTERNS:-}"
# Ambil hanya level pertama (permukaan). Set LEV_IDX=all untuk skip subset.
LEV_IDX="${LEV_IDX:-0}"

SRC_DIR="${SRC_DIR//$'\r'/}"
DST_DIR="${DST_DIR//$'\r'/}"
LOG_DIR="${LOG_DIR//$'\r'/}"
LOG_FILE="${LOG_FILE//$'\r'/}"
FORCE="${FORCE//$'\r'/}"
FIND_MAXDEPTH="${FIND_MAXDEPTH//$'\r'/}"
FIND_NAME="${FIND_NAME//$'\r'/}"
GLOB_PATTERNS="${GLOB_PATTERNS//$'\r'/}"
LEV_IDX="${LEV_IDX//$'\r'/}"

mkdir -p "$DST_DIR" "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

exec > >(tee -a "$LOG_FILE") 2>&1
_finish() { sleep 0.2 2>/dev/null || true; }
trap _finish EXIT

have_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! have_cmd cdo && ! have_cmd ncks; then
  log "ERROR: butuh cdo atau ncks (NCO) di PATH"
  exit 1
fi

# InaNWP asim (ncdump nyata di litbangweb) — lowercase + lat/lon/time
# Jangan ambil u,v,temp,rh,td,theta,dbz,wspd,wdir (profil 3D palsu di lev)
HARP_ASIM="
lat lon time lev
t2m td2m rh2m mslp pres
u10 v10 ws10 wd10
rain rainc rainnc rainsh
clflo clfmi clfhi
"

# Cadangan wrfout WRF mentah (jika ada)
HARP_WRF="
XLAT XLONG XTIME Times HGT
T2 T2M Q2 RH2 PSFC MSLP
U10 V10 WS10 WD10
RAINC RAINNC RAINSH
"

list_candidate_nc() {
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
  find "$SRC_DIR" "${depth[@]}" -type f \( \
    -name '*asim*.nc' -o -name '*asim*.NC' -o \
    -name 'wrfout_d01_*' -o -name '*-d01-*.nc' \
  \) 2>/dev/null | sort -u
}

# Nama variabel di file (ncdump -h)
file_var_names() {
  local f="$1"
  if have_cmd ncdump; then
    ncdump -h "$f" 2>/dev/null | awk '
      $1 ~ /^(float|double|int|short|byte|char|uint|int64|uint64)/ {
        n=$2; sub(/\(.*$/,"",n); gsub(/;/,"",n); print n
      }
    '
    return
  fi
  if have_cmd cdo; then
    cdo -s showname "$f" 2>/dev/null | tr ' ' '\n' | grep -E '^[A-Za-z_]' || true
  fi
}

detect_format() {
  local f="$1" names
  names=$(file_var_names "$f" | tr '\n' ' ')
  case " $names " in
    *" t2m "*|*" rainnc "*|*" u10 "*) echo asim ;;
    *" T2 "*|*" XLAT "*|*" RAINNC "*) echo wrf ;;
    *) echo asim ;;
  esac
}

intersect_vars() {
  local f="$1" wanted="$2" present out
  present=$(file_var_names "$f" | sort -u)
  wanted=$(echo "$wanted" | tr ' ' '\n' | grep -E '^[A-Za-z]' | sort -u)
  out=$(comm -12 <(echo "$present") <(echo "$wanted") | tr '\n' ',' | sed 's/,$//')
  echo "$out"
}

has_dim() {
  local f="$1" dim="$2"
  ncdump -h "$f" 2>/dev/null | grep -Eq "^[[:space:]]*${dim}[[:space:]]*="
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
  if [ "$ss" -gt 0 ] && [ "$ds" -gt $((ss * 8 / 10)) ]; then
    return 0
  fi
  return 1
}

crop_one() {
  local src="$1"
  local base dest fmt wanted vars tmp ok=0
  base="$(basename "$src")"
  base="${base//$'\r'/}"
  dest="$DST_DIR/$base"

  if ! need_crop "$src" "$dest"; then
    local ss ds
    ss=$(stat -c%s "$src" 2>/dev/null || echo 0)
    ds=$(stat -c%s "$dest" 2>/dev/null || echo 0)
    log "SKIP $base (sudah crop $(awk -v d="$ds" -v s="$ss" 'BEGIN{printf "%.0f MB, src %.2f GB", d/1e6, s/1e9}'))"
    return 0
  fi

  fmt=$(detect_format "$src")
  if [ "$fmt" = wrf ]; then
    wanted="$HARP_WRF"
  else
    wanted="$HARP_ASIM"
  fi
  vars=$(intersect_vars "$src" "$wanted")
  if [ -z "$vars" ]; then
    log "WARN $base: tidak ketemu var HARP — fallback asim inti"
    vars="lat,lon,time,t2m,td2m,rh2m,mslp,u10,v10,ws10,wd10,rain,rainc,rainnc,clflo"
  fi

  tmp="${dest}.tmp.$$"
  rm -f "$tmp"
  log "CROP $base  format=$fmt  lev=$LEV_IDX  vars=$vars"

  # Bash 4.3 + set -u: "${arr[@]}" kosong → unbound variable (Ubuntu 16.04)
  local ncks_cmd=(ncks -O -v "$vars")
  if [ "$LEV_IDX" != "all" ] && has_dim "$src" lev; then
    ncks_cmd+=(-d "lev,$LEV_IDX")
  fi

  if have_cmd ncks; then
    if "${ncks_cmd[@]}" "$src" "$tmp" 2>/tmp/monas_ncks.err; then
      ok=1
    else
      log "ncks gagal: $(tr '\n' ' ' </tmp/monas_ncks.err | head -c 400)"
      if ncks -O -v "$vars" "$src" "$tmp" 2>/tmp/monas_ncks2.err; then
        ok=1
        if [ "$LEV_IDX" != "all" ] && has_dim "$tmp" lev; then
          ncks -O -d "lev,$LEV_IDX" "$tmp" "${tmp}.lev" 2>/dev/null && mv "${tmp}.lev" "$tmp" || true
        fi
      else
        log "ncks retry gagal: $(tr '\n' ' ' </tmp/monas_ncks2.err | head -c 300)"
      fi
    fi
  fi

  if [ "$ok" != "1" ] && have_cmd cdo; then
    local cdo_in="$src"
    # CDO: selname lalu sellevidx jika ada
    if cdo -s -f nc selname,"$vars" "$src" "$tmp" 2>/tmp/monas_cdo.err; then
      ok=1
      if [ "$LEV_IDX" != "all" ] && has_dim "$tmp" lev; then
        # sellevidx 1-based
        local idx=$((LEV_IDX + 1))
        if cdo -s sellevidx,"$idx" "$tmp" "${tmp}.lev" 2>/dev/null; then
          mv "${tmp}.lev" "$tmp"
        fi
      fi
    else
      log "cdo selname gagal: $(tr '\n' ' ' </tmp/monas_cdo.err | head -c 400)"
      # Merge var satu-satu
      local first=1 piece="${tmp}.p"
      rm -f "$piece" "${tmp}.acc"
      IFS=',' read -r -a arr <<< "$vars"
      for v in "${arr[@]}"; do
        [ -z "$v" ] && continue
        if cdo -s -f nc selname,"$v" "$cdo_in" "$piece" 2>/dev/null; then
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
  log "OK $base  $(awk -v s="$ss" -v d="$ds" 'BEGIN{printf "%.2f GB → %.0f MB (%.1f%%)", s/1e9, d/1e6, 100.0*d/s}')"
}

log "=== crop InaNWP HARP  SRC=$SRC_DIR  DST=$DST_DIR  FORCE=$FORCE  LEV_IDX=$LEV_IDX ==="

if [ ! -d "$SRC_DIR" ]; then
  log "ERROR: SRC_DIR tidak ada: $SRC_DIR"
  exit 1
fi

mapfile -t FILES < <(list_candidate_nc || true)
count=0
fail=0
seen=""

if [ "${#FILES[@]}" -eq 0 ] || [ -z "${FILES[0]:-}" ]; then
  log "WARN: tidak ada file cocok di $SRC_DIR (maxdepth=$FIND_MAXDEPTH)"
  ls -lah "$SRC_DIR" 2>/dev/null | head -40 | while read -r line; do log "  $line"; done
  find "$SRC_DIR" -maxdepth "$FIND_MAXDEPTH" -type f -name '*.nc' 2>/dev/null | head -20 | while read -r p; do
    log "  $p ($(stat -c%s "$p" 2>/dev/null || echo '?') bytes)"
  done
  exit 1
fi

log "Ditemukan ${#FILES[@]} kandidat NC"
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in *.tmp|*.tmp.*) continue ;; esac
  case " $seen " in *" $f "*) continue ;; esac
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
[ "$count" -eq 0 ] && exit 1
[ "$fail" -gt 0 ] && exit 1
exit 0
