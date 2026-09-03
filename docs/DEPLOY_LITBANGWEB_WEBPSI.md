# Deploy arsitektur: PC obs → litbangweb compute (Docker) → webpsi serve

## Peran mesin

| Mesin | Jaringan | Tugas |
|-------|---------|--------|
| **PC BMKG** | Intranet BMKG (API obs) | Fetch observasi harian → SFTP ke litbangweb `monas_obs` · build Docker image (online) |
| **litbangweb** (`puslitbang`) | **Tanpa internet** · Docker | Verifikasi HARP dari NC lokal + obs JSON → export artifact ringan |
| **webpsi** | Publik/portal | Apache + PM2 · `SERVE_READONLY` · tampilkan dashboard |

```
PC 02:00  fetch obs BMKG → SFTP → /opt/lampp/htdocs/wrf/monas_obs
litbangweb 04:00  CDO/ncks crop wrfout 12GB → /opt/lampp/htdocs/wrf/monas_nc (2D HARP)
litbangweb 04:30  docker monas-compute (baca monas_nc) → export artifact → SCP webpsi
webpsi  import artifact → https://psimkg.bmkg.go.id/monas/
```

Stack UI **tetap** FastAPI + Canvas (bukan React). Latency diatasi precompute, bukan rewrite frontend.

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
# Edit compute.env — tambah jika sync otomatis ke webpsi:
#   WEBPSI_HOST=10.21.224.196
#   WEBPSI_USER=vmhosting
#   WEBPSI_SSH_PORT=22

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
export WEBPSI_HOST=10.21.224.196 WEBPSI_USER=vmhosting   # opsional
/opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh
```

`RUN_CROP=true` (default): crop dulu jika file baru, lalu Docker HARP. Mount:
- NC: `/opt/lampp/htdocs/wrf/monas_nc` → `/data/nc`
- Obs: `/opt/lampp/htdocs/wrf/monas_obs` → `/data/obs`
- Data: `/opt/lampp/htdocs/monas/compute-data` → `/app/data`

### C2. Cron
```cron
0 4 * * * /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
30 4 * * * /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh >> /opt/lampp/htdocs/monas/logs/compute.log 2>&1
```

Compute boleh `RUN_CROP=true` (idempotent). Default: `USE_DUMMY_MODELS=false`.

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

Import artifact (otomatis dari script litbangweb, atau manual):
```bash
./scripts/sync_artifacts_to_webpsi.sh /var/www/monas/data/artifacts/latest
```

Cek: `curl -s http://127.0.0.1:8013/api/health` → `"mode":"readonly"`.

---

## Jadwal harian (ringkas)

| Waktu | Mesin | Aksi |
|-------|--------|------|
| 02:00 | PC | `daily_obs_pc.bat` → monas_obs |
| 04:00 | litbangweb | CDO crop wrfout → monas_nc |
| 04:30 | litbangweb | Docker HARP (monas_nc) + export + SCP artifact |
| ~04:45 | webpsi | import + restart API (dari script) |

---

## Catatan Ubuntu 16.04

Host litbangweb **16.04** — jangan `pip install` langsung di host. Semua dependency ada **di dalam image** `monas-compute` (Python 3.11 / Debian bookworm).

Rebuild image di PC bila `requirements.txt` berubah, lalu `docker load` ulang di litbangweb.

**Crop CDO tidak butuh rebuild image** — script host + ganti mount ke `monas_nc` cukup. Overlay `backend/` hanya jika ingin penjumlahan `RAINC+RAINNC` di image lama.

---

## Langkah sekarang (PC → litbangweb → webpsi)

### 1. PC lokal
1. `git pull origin main` (setelah PR ini merge).
2. Obs harian tetap: Task Scheduler `scripts\daily_obs_pc.bat` jam 02:00. **Tidak perlu** jalankan verify HARP di PC.
3. Dashboard `localhost:3013` kosong sampai artifact dari litbangweb/webpsi di-import — itu normal.
4. Copy script baru ke litbangweb (server tanpa git/internet):
   ```bat
   scp -P 3346 scripts\crop_inanwp_cdo.sh scripts\litbangweb_daily_compute.sh litbangweb@202.90.199.54:/tmp/
   ```
   Opsional overlay Python (hujan RAINC+RAINNC): scp folder `backend\services\nc_reader.py` + `backend\config.py`.

### 2. litbangweb (`puslitbang`)
```bash
# Hentikan job wrfout 12GB yang masih hidup
docker ps
docker stop 0414bf23e925    # ganti id jika beda

sudo mkdir -p /opt/lampp/htdocs/monas/scripts /opt/lampp/htdocs/monas/logs /opt/lampp/htdocs/wrf/monas_nc
sudo cp /tmp/crop_inanwp_cdo.sh /tmp/litbangweb_daily_compute.sh /opt/lampp/htdocs/monas/scripts/
sudo sed -i 's/\r$//' /opt/lampp/htdocs/monas/scripts/*.sh
sudo chmod +x /opt/lampp/htdocs/monas/scripts/*.sh

which cdo; cdo -V | head -1
which ncks || echo "ncks opsional (lebih ramah WRF)"

# Crop sekali — pantau log
sudo /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
ls -lh /opt/lampp/htdocs/wrf/monas_nc/
tail -f /opt/lampp/htdocs/monas/logs/cdo_crop.log

# HARP dari file crop (cepat)
sudo /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh
ls /opt/lampp/htdocs/monas/compute-data/artifacts/latest/

crontab -e
# 0 4 * * * /opt/lampp/htdocs/monas/scripts/crop_inanwp_cdo.sh
# 30 4 * * * /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh >> /opt/lampp/htdocs/monas/logs/compute.log 2>&1
```

Set `WEBPSI_HOST` / `WEBPSI_USER` di `compute.env` jika sync artifact otomatis.

### 3. webpsi
Jika script compute sudah SSH ke webpsi: import + `pm2 restart monas-api` otomatis.

Manual:
```bash
cd /var/www/monas
# .env: SERVE_READONLY=true
./scripts/sync_artifacts_to_webpsi.sh /var/www/monas/data/artifacts/latest
# atau: .venv/bin/python scripts/import_artifacts.py --from /var/www/monas/data/artifacts/latest
pm2 restart monas-api
curl -s http://127.0.0.1:8013/api/health
```

Cek UI: https://psimkg.bmkg.go.id/monas/

---

## Checklist

- [ ] PC: Task Scheduler 02:00 `daily_obs_pc.bat`
- [ ] PC: pernah `build_compute_image.bat` + copy tar ke litbangweb
- [ ] litbangweb: `docker images | grep monas-compute`
- [ ] litbangweb: crop CDO → `ls /opt/lampp/htdocs/wrf/monas_nc/`
- [ ] litbangweb: cron 04:00 crop + 04:30 `litbangweb_daily_compute.sh`
- [ ] webpsi: `SERVE_READONLY=true`, PM2 monas-api/web
- [ ] URL: https://psimkg.bmkg.go.id/monas/
