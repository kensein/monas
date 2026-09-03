# Deploy arsitektur: PC obs → litbangweb compute (Docker) → webpsi serve

## Peran mesin

| Mesin | Jaringan | Tugas |
|-------|---------|--------|
| **PC BMKG** | Intranet BMKG (API obs) | Fetch observasi harian → SFTP ke litbangweb `monas_obs` · build Docker image (online) |
| **litbangweb** (`puslitbang`) | **Tanpa internet** · Docker | Verifikasi HARP dari NC lokal + obs JSON → export artifact ringan |
| **webpsi** | Publik/portal | Apache + PM2 · `SERVE_READONLY` · tampilkan dashboard |

```
PC 02:00  fetch obs BMKG (JSON full param) → SFTP → /opt/lampp/htdocs/wrf/monas_obs
litbangweb 04:00  CDO/ncks crop wrfout 12GB → /opt/lampp/htdocs/wrf/monas_nc (2D, ~300MB)
litbangweb 04:30  docker monas-compute: interp → obs (param HARP saja) → det_verify
                  → f32 store + staging /opt/lampp/htdocs/wrf/monas_export/
webpsi/PC  ~05:00  SFTP pull :3346 dari litbangweb (pola PSIIDN) → /var/www/monas/data/artifacts/
webpsi  API baca f32 store langsung (tanpa import) → psimkg.bmkg.go.id/monas/
```

> **Jaringan:** litbangweb biasanya **tidak** bisa `rsync` push ke `webpsi:22` (timeout).
> Samakan dengan PSIIDN: **webpsi (atau PC hub) yang pull** via SFTP `202.90.199.54:3346`.
> Jangan set `WEBPSI_HOST` di litbangweb kecuali push terbukti jalan.

Stack UI **tetap** FastAPI + Canvas (bukan React). Latency diatasi precompute, bukan rewrite frontend.

## Format store (PSIIDN-style, tanpa SQLite)

```
data/artifacts/                 (webpsi & litbangweb sama)
├── manifest.json               index run, stasiun, param, exported_at
├── obs/<YYYYMM>/index.json     axis: station_ids, params, jam dalam bulan
├── obs/<YYYYMM>/values.f32     float32 [n_jam, n_stasiun, n_param]  (~9 MB/bulan)
└── runs/<model>/<YYYYMMDDHH>/
    ├── meta.json               init, lead_times, station_ids, info NC
    ├── fcst.f32                float32 [n_param, n_lead, n_stasiun]
    ├── obs.f32                 obs paired pada valid_time (sama shape)
    └── scores.f32              float32 [n_param, n_lead, 7] bias,rmse,mae,stde,corr,n_cases,n_stations
```

Satu run ≈ 1–2 MB. HARP (interpolasi, join paired, det_verify, ranking) **tidak berubah** — hanya I/O.
Obs JSON di litbangweb tetap **full parameter**; filter ke `VERIFY_PARAMETERS` terjadi saat build cube.

Run yang sudah ada di store (nama+ukuran NC sama) **tidak dihitung ulang**. Cube obs per bulan
di-parse ulang hanya jika file JSON bulan itu berubah.

---

## A. PC — fetch obs harian (otomatis)

### A1. `.env` PC (cuplikan)
```env
BMKG_USERNAME=psimkg
BMKG_PASSWORD=...
OBS_EXPORT_DIR=D:\nwp-data\obs
USE_LOCAL_OBS_JSON=true

SFTP_HOST=202.90.199.54
SFTP_PORT=3346
SFTP_USER=litbangweb
SFTP_PASSWORD=...
SFTP_OBS_PATH=/opt/lampp/htdocs/wrf/monas_obs
DISABLE_SFTP=false
```
> Untuk sync obs, `DISABLE_SFTP` harus **false** (atau jangan set true) agar upload jalan. Hitungan verify di PC tidak wajib.

### A2. Task Scheduler — jam **02:00**
| Field | Nilai |
|-------|--------|
| Program | `...\monas\scripts\daily_obs_pc.bat` |
| Start in | `...\monas` |

Manual uji:
```bat
scripts\daily_obs_pc.bat
```
Isi: `fetch_obs_local.py --days 3 --monthly --sync` → JSON di `D:\nwp-data\obs` + SFTP ke litbangweb.

Tidak perlu `--from-june` tiap hari (hemat waktu); full history sudah pernah diisi.

---

## B. Build Docker image (PC online **atau** mesin lain)

Butuh **Docker**. Di PC Anda sebelumnya error `docker is not recognized` = Docker belum terpasang / belum di PATH.

### Opsi B1 — Install Docker Desktop di PC (disarankan)
1. Install: https://docs.docker.com/desktop/setup/install/windows-install/
2. Buka **Docker Desktop**, tunggu status Engine running
3. Tutup + buka ulang PowerShell
4. Di folder monas:
   ```bat
   git pull origin main
   scripts\build_compute_image.bat
   scp -P 3346 dist\monas-compute.tar litbangweb@202.90.199.54:/home/litbangweb/
   ```

### Opsi B2 — Build di webpsi / Linux yang ada Docker + internet
```bash
git clone https://github.com/kensein/monas.git && cd monas   # atau git pull
docker build -f docker/compute/Dockerfile -t monas-compute:latest .
docker save monas-compute:latest -o monas-compute.tar
scp -P 3346 monas-compute.tar litbangweb@202.90.199.54:/home/litbangweb/
```

### Opsi B3 — Tanpa Docker di PC
Hanya jalankan **obs harian** di PC (`daily_obs_pc.bat`). Image di-build di mesin B2.

---

## C. litbangweb — install compute (sekali)

SSH ke `puslitbang` (port 3346):

```bash
# Load image
docker load -i /home/litbangweb/monas-compute.tar
# atau: gunzip -c monas-compute.tar.gz | docker load

# Deploy script + env (dari repo atau copy manual)
sudo mkdir -p /opt/lampp/htdocs/monas/{scripts,logs,compute-data}
sudo mkdir -p /opt/lampp/htdocs/wrf/monas_nc
sudo cp scripts/litbangweb_daily_compute.sh scripts/crop_inanwp_cdo.sh /opt/lampp/htdocs/monas/scripts/
sudo chmod +x /opt/lampp/htdocs/monas/scripts/*.sh

sudo cp docker/compute/env.litbangweb.example /opt/lampp/htdocs/monas/compute.env
# Jangan set WEBPSI_HOST kecuali litbangweb→webpsi:22 terbukti (sering timeout).
# Sync = pull dari webpsi/PC (pola PSIIDN) dari /opt/lampp/htdocs/wrf/monas_export

# Atau pakai installer:
sudo bash scripts/install_litbangweb_compute.sh /home/litbangweb/monas-compute.tar
```

### C0. Crop CDO (wajib — jangan HARP-kan wrfout 12GB)

File InaNWP di litbangweb adalah **`*-d01-asim.nc`** (turunan GrADS/CF: `t2m`,`u10`,`rain`,…), **bukan** wrfout WRF (`T2`,`XLAT`). Semua field punya `lev=19` → crop wajib:
1. pilih var HARP saja, dan  
2. `-d lev,0` (permukaan).

```bash
# Uji 1 file (nama var benar)
SRC=/opt/lampp/htdocs/wrf/wrfout/2026090200-d01-asim.nc
DST=/opt/lampp/htdocs/wrf/monas_nc/2026090200-d01-asim.nc
ncks -O -v lat,lon,time,t2m,td2m,rh2m,mslp,pres,u10,v10,ws10,wd10,rain,rainc,rainnc,clflo,clfmi,clfhi \
  -d lev,0 "$SRC" "$DST"
ls -lh "$DST"   # target << 1GB (sering ~100–400MB)
```

Atau script:
```bash
/opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
ls -lh /opt/lampp/htdocs/wrf/monas_nc/
```

Jika job Docker lama masih baca wrfout 12GB, hentikan dulu:
```bash
docker stop cranky_bardeen   # atau: docker ps  lalu docker stop <id>
```

### C1. Uji compute (baca **monas_nc**, bukan wrfout)
```bash
# Jangan set WEBPSI_HOST (push ke :22 sering timeout)
/opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh
# Staging SFTP: /opt/lampp/htdocs/wrf/monas_export/{manifest.json,runs,obs}
```

`RUN_CROP=true` (default): crop dulu jika file baru, lalu Docker HARP v2. Mount:
- NC: `/opt/lampp/htdocs/wrf/monas_nc` → `/data/nc`
- Obs: `/opt/lampp/htdocs/wrf/monas_obs` → `/data/obs`
- Store: `/opt/lampp/htdocs/monas/compute-data/artifacts` → `/app/data/artifacts`

Env penting (`compute.env` atau export sebelum script):

| Var | Default | Arti |
|-----|---------|------|
| `VERIFY_MAX_RUNS` | `1` | run terbaru saja; `0` = backfill semua NC di `monas_nc` |
| `KEEP_RUNS_PER_MODEL` | `0` | prune run lama (mis. `90`); `0` = simpan semua |
| `HARP_EXTRA_ARGS` | — | `--force` hitung ulang, `--force-obs` parse ulang JSON obs |
| `CODE_DIR` | — | overlay `backend/`+`scripts/` tanpa rebuild image |

Log yang diharapkan (live):
```
1/4 Scan NC crop...         6 NC ditemukan · pending=1
2/4 Observasi → cube f32    [obs 202609] (1/3) sinoptik_... (28 MB)
3/4 Verifikasi HARP         [ 45.0%] Param temp_drybulb_c_tttttt (1/14) … det_verify … Tersimpan (…s)
4/4 Manifest + prune        manifest: N run · store XX MB
```

### C2. Cron
```cron
0 4 * * * /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
30 4 * * * /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh >> /opt/lampp/htdocs/monas/logs/compute.log 2>&1
```

Backfill sejarah bertahap (malam): `VERIFY_MAX_RUNS=2 …/litbangweb_daily_compute.sh`.

---

## D. webpsi — dashboard (Apache + PM2)

Lihat juga `DEPLOY_MONAS.md`.

```bash
cd /var/www/monas
git pull origin main
# .env:
#   SERVE_READONLY=true
#   ENABLE_PIPELINE_SCHEDULER=false
#   FORCE_PIPELINE=false
#   SEED_DEMO_DATA=false
#   BASE_PATH=/monas
./deploy_monas.sh
# atau: pm2 startOrReload ecosystem.config.cjs
```

**Tidak ada langkah import.** API membaca `data/artifacts/manifest.json` + `runs/` + `obs/` langsung
(cache di memori, refresh otomatis saat `manifest.json` berubah).

### Sync artifact — pola PSIIDN (pull, bukan push)

litbangweb **stage** ke folder SFTP-accessible setelah compute:
`/opt/lampp/htdocs/wrf/monas_export/` (= salinan `compute-data/artifacts`).

**Opsi A — dari webpsi** (jika webpsi bisa reach `202.90.199.54:3346`):
```bash
cd /var/www/monas
./scripts/pull_artifacts_from_litbangweb.sh
# butuh SSH key / password untuk user litbangweb
```

**Opsi B — hub PC** (paling andal bila webpsi ↔ litbangweb tidak langsung):
```bat
REM di PC BMKG (bisa SFTP litbangweb + SSH webpsi)
set WEBPSI_HOST=10.21.224.196
set WEBPSI_USER=vmhosting
scripts\pull_artifacts_via_pc.bat
```

**Opsi C — manual sekali:**
```bash
# litbangweb: pastikan staging ada
ls /opt/lampp/htdocs/wrf/monas_export/manifest.json
# atau stage manual sekarang:
sudo mkdir -p /opt/lampp/htdocs/wrf/monas_export
sudo rsync -a --delete /opt/lampp/htdocs/monas/compute-data/artifacts/{manifest.json,runs,obs} \
  /opt/lampp/htdocs/wrf/monas_export/

# PC / webpsi pull:
scp -P 3346 -r litbangweb@202.90.199.54:/opt/lampp/htdocs/wrf/monas_export/{manifest.json,runs,obs} \
  /var/www/monas/data/artifacts/
```

> Jangan `rsync` dari litbangweb ke `10.21.224.196:22` — biasanya **Connection timed out**.

Cek: `curl -s http://127.0.0.1:8013/api/health` → `{"mode":"readonly","store":"f32"}` dan
`curl -s http://127.0.0.1:8013/api/pipeline/status | head -c 300`.

Legacy SQLite (`import_artifacts.py`, `dashboard.sqlite`) masih ada untuk `STORE_BACKEND=sqlite`, tidak dipakai default.

---

## Jadwal harian (ringkas)

| Waktu | Mesin | Aksi |
|-------|--------|------|
| 02:00 | PC | `daily_obs_pc.bat` → monas_obs |
| 04:00 | litbangweb | CDO crop wrfout → monas_nc |
| 04:30 | litbangweb | Docker HARP v2 → f32 store + stage `monas_export` |
| ~05:00 | webpsi/PC | SFTP pull :3346 (pola PSIIDN) → `data/artifacts/` |
| ~05:05 | webpsi | API otomatis baca manifest baru (tanpa import/restart) |

---

## Catatan Ubuntu 16.04

Host litbangweb **16.04** — jangan `pip install` langsung di host. Semua dependency ada **di dalam image** `monas-compute` (Python 3.11 / Debian bookworm).

Rebuild image di PC bila `requirements.txt` berubah, lalu `docker load` ulang di litbangweb.

**Crop CDO tidak butuh rebuild image** — script host + ganti mount ke `monas_nc` cukup. Overlay `backend/` hanya jika ingin penjumlahan `RAINC+RAINNC` di image lama.

---

## Langkah sekarang (PC → litbangweb → webpsi)

### 1. PC lokal
1. `git pull origin main`.
2. Obs harian tetap: Task Scheduler `scripts\daily_obs_pc.bat` jam 02:00 (JSON **full parameter** — jangan diubah).
3. **Tidak perlu** verify HARP di PC.
4. Kirim kode baru ke litbangweb (tanpa git/internet di server). Dua opsi:
   - **Overlay (cepat, tanpa rebuild image):**
     ```bat
     scp -P 3346 scripts\crop_inanwp_cdo.sh scripts\litbangweb_daily_compute.sh litbangweb@202.90.199.54:/tmp/
     scp -P 3346 -r backend litbangweb@202.90.199.54:/tmp/backend
     scp -P 3346 -r scripts litbangweb@202.90.199.54:/tmp/scripts
     ```
   - **Rebuild image** (rapi, sekali): `scripts\build_compute_image.bat` → scp tar → `docker load`.

### 2. litbangweb (`puslitbang`)
```bash
docker ps; docker stop <id_job_lama>   # jika masih ada job SQLite lama

sudo mkdir -p /opt/lampp/htdocs/monas/{scripts,logs,src} /opt/lampp/htdocs/wrf/monas_nc
sudo cp /tmp/crop_inanwp_cdo.sh /tmp/litbangweb_daily_compute.sh /opt/lampp/htdocs/monas/scripts/
sudo sed -i 's/\r$//' /opt/lampp/htdocs/monas/scripts/*.sh
sudo chmod +x /opt/lampp/htdocs/monas/scripts/*.sh

# Overlay kode (jika tidak rebuild image)
sudo rm -rf /opt/lampp/htdocs/monas/src/backend /opt/lampp/htdocs/monas/src/scripts
sudo cp -a /tmp/backend /opt/lampp/htdocs/monas/src/backend
sudo cp -a /tmp/scripts /opt/lampp/htdocs/monas/src/scripts
sudo find /opt/lampp/htdocs/monas/src \( -name '*.py' -o -name '*.sh' \) -exec sed -i 's/\r$//' {} \;

# Wajib ada (overlay menimpa /app/backend di container — scp tidak lengkap → ModuleNotFoundError)
ls -la /opt/lampp/htdocs/monas/src/backend/services/harp_compute.py \
       /opt/lampp/htdocs/monas/src/backend/services/harp_store.py \
       /opt/lampp/htdocs/monas/src/scripts/harp_compute.py

# Store lama (SQLite) tidak dipakai lagi — boleh dipindah
sudo mv /opt/lampp/htdocs/monas/compute-data/nwp_verify.db /opt/lampp/htdocs/monas/compute-data/nwp_verify.db.bak 2>/dev/null || true

# Crop (skip jika sudah 6 file di monas_nc)
sudo /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
ls -lh /opt/lampp/htdocs/wrf/monas_nc/

# HARP v2 — run terbaru
export CODE_DIR=/opt/lampp/htdocs/monas/src
export VERIFY_MAX_RUNS=1
sudo -E /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh
# SSH lain: tail -f /opt/lampp/htdocs/monas/logs/compute.log

ls -R /opt/lampp/htdocs/monas/compute-data/artifacts | head -30
cat /opt/lampp/htdocs/monas/compute-data/artifacts/manifest.json | head -40

# Backfill 5 NC lain (malam): VERIFY_MAX_RUNS=0

crontab -e
# 0 4 * * * /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
# 30 4 * * * CODE_DIR=/opt/lampp/htdocs/monas/src /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh >> /opt/lampp/htdocs/monas/logs/compute.log 2>&1
```

> Script host **tidak** memakai entrypoint image; ia langsung menjalankan
> `python scripts/harp_compute.py` di dalam container. Dengan `CODE_DIR` (overlay `backend/` + `scripts/`)
> image lama pun sudah menjalankan HARP v2 (f32) — rebuild image opsional.
> Overlay **harus** berisi `backend/services/harp_compute.py` + `harp_store.py` + `scripts/harp_compute.py`
> (dari `main` setelah PR #23). Overlay tidak lengkap → `ModuleNotFoundError` / exit 2 preflight.

Set `WEBPSI_HOST` hanya jika push litbangweb→webpsi:22 terbukti. Default: staging
`/opt/lampp/htdocs/wrf/monas_export` + pull dari webpsi/PC.

### 3. webpsi (setup awal + pull)
```bash
# setup sekali — lihat DEPLOY_MONAS.md
sudo mkdir -p /var/www/monas && sudo chown $USER:$USER /var/www/monas
git clone https://github.com/kensein/monas.git /var/www/monas
cd /var/www/monas && cp .env.example .env
# .env: SERVE_READONLY=true  SEED_DEMO_DATA=false  BASE_PATH=/monas
./deploy_monas.sh
mkdir -p data/artifacts

# sync store (pola PSIIDN — pull, bukan push dari litbangweb)
./scripts/pull_artifacts_from_litbangweb.sh
# atau dari PC: scripts\pull_artifacts_via_pc.bat

curl -s http://127.0.0.1:8013/api/health          # {"mode":"readonly","store":"f32"}
curl -s http://127.0.0.1:8013/api/cycles | head -c 300
```

Cek UI: https://psimkg.bmkg.go.id/monas/

---

## Checklist

- [ ] PC: Task Scheduler 02:00 `daily_obs_pc.bat`
- [ ] PC: pernah `build_compute_image.bat` + copy tar ke litbangweb
- [ ] litbangweb: `docker images | grep monas-compute`
- [ ] litbangweb: crop CDO → `ls /opt/lampp/htdocs/wrf/monas_nc/`
- [ ] litbangweb: `compute-data/artifacts/manifest.json` ada, `runs/InaNWP/<init>/scores.f32`
- [ ] litbangweb: cron 04:00 crop + 04:30 `litbangweb_daily_compute.sh` (CODE_DIR jika overlay)
- [ ] webpsi: `SERVE_READONLY=true`, PM2 monas-api/web, `data/artifacts/` via SFTP pull (pola PSIIDN)
- [ ] URL: https://psimkg.bmkg.go.id/monas/
