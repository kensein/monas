# MONAS — NWP Verification Dashboard (HARP)

Dashboard **display-only** verifikasi titik (harpPoint-style) untuk model NWP BMKG.
User **tidak** upload NC. Hitung di **litbangweb** (Docker, offline) → artifact **f32** → **webpsi** hanya serve.

| | |
|--|--|
| **Produksi** | https://psimkg.bmkg.go.id/monas/ |
| **Repo** | https://github.com/kensein/monas |
| **Stack UI** | FastAPI + static HTML/JS + **Canvas 2D** (bukan React/Plotly) |
| **Store** | `STORE_BACKEND=f32` (default) — PSIIDN-style float32 + JSON; SQLite legacy opsional |

Dokumen detail: `docs/DEPLOY_LITBANGWEB_WEBPSI.md` · handoff agent: `AGENT_BRIEF.md` · deploy portal: `DEPLOY_MONAS.md`

---

## Arsitektur produksi (saat ini)

```
┌──────────────┐   obs JSON    ┌─────────────────────────────┐
│  PC BMKG     │ ───────────►  │  litbangweb (tanpa internet) │
│  fetch BMKG  │   SFTP :3346  │  wrfout/*.nc (~12GB)         │
│  API sinoptik│               │       │ CDO/ncks crop 2D     │
└──────────────┘               │       ▼                      │
                               │  monas_nc/*-asim.nc (~0.3GB) │
                               │  monas_obs/*.json            │
                               │       │ docker monas-compute │
                               │       ▼                      │
                               │  interp → join → QC → det_verify
                               │  f32 store + monas_export/   │
                               └──────────────┬──────────────┘
                                              │ pull SFTP :3346
                                              │ (litbangweb TIDAK push ke webpsi:22)
                                              ▼
                               ┌─────────────────────────────┐
                               │  webpsi                      │
                               │  SERVE_READONLY=true         │
                               │  Apache + PM2 monas-api/web  │
                               │  baca f32 → UI /monas/       │
                               └─────────────────────────────┘
```

### Peran mesin

| Mesin | Jaringan | Tugas |
|-------|---------|--------|
| **PC BMKG** | Intranet BMKG | Fetch obs harian → SFTP `monas_obs`; (opsional) build Docker image |
| **litbangweb** | Offline + Docker | Crop NC → HARP compute → stage `monas_export/` |
| **webpsi** | Portal publik | `SERVE_READONLY` — API + frontend saja |

> **Jaringan penting:** litbangweb biasanya **tidak** bisa `rsync`/`scp` ke `webpsi:22` (timeout).  
> Sync artifact = **webpsi atau PC yang pull** dari litbangweb `202.90.199.54:3346` (pola PSIIDN).

### Jadwal harian (ringkas)

| Waktu | Mesin | Aksi |
|-------|-------|------|
| ~02:00 | PC | `scripts/daily_obs_pc.bat` → JSON + SFTP `monas_obs` |
| ~04:00 | litbangweb | `crop_inanwp_cdo.sh` → `monas_nc/` |
| ~04:30 | litbangweb | `litbangweb_daily_compute.sh` (Docker) → f32 + `monas_export/` |
| ~05:00 | webpsi/PC | `pull_artifacts_from_litbangweb.sh` → `data/artifacts/` → `pm2 restart` |

Panduan lengkap: **`docs/DEPLOY_LITBANGWEB_WEBPSI.md`**.

Cadangan hitung di PC: `docs/DAILY_PC_WEBPSI_FLOW.md`.

---

## Store f32 (bukan SQLite untuk skor)

```
data/artifacts/
├── manifest.json
├── obs/<YYYYMM>/
│   ├── index.json
│   └── values.f32          # [n_jam, n_stasiun, n_param]
└── runs/<model>/<YYYYMMDDHH>/
    ├── meta.json
    ├── fcst.f32            # [n_param, n_lead, n_stasiun]
    ├── obs.f32             # paired obs, shape sama
    └── scores.f32          # [n_param, n_lead, 7]
                            # bias, rmse, mae, stde, corr, n_cases, n_stations
```

- Satu run ≈ 1–2 MB. UI **tidak** menghitung HARP ulang.
- `SERVE_READONLY=true` di webpsi: matikan pipeline/scheduler/SFTP inventory.
- Env: `STORE_BACKEND=f32` (default) · `SERVE_READONLY=true` (webpsi).

---

## Metodologi HARP (point)

Alur: **Read FCST → Read OBS → Join → QC → common_cases → det_verify**

| Skor | Formula |
|------|---------|
| Bias | mean(fcst − obs) |
| RMSE | √mean((fcst − obs)²) |
| MAE | mean(\|fcst − obs\|) |
| stde | std(fcst − obs, ddof=1) |
| Correlation | Pearson(fcst, obs) |

**QC observasi** (`backend/services/obs_qc.py`):

1. Sentinel BMKG **8888 / 9999** (`|nilai| ≥ 8888`) → **buang (NaN)**, jangan set 0  
2. Lalu outlier **\|error\| > 4σ** (`check_obs_against_fcst`)

**InaNWP asim NC** hanya field permukaan (`t2m`, `td2m`, `rh2m`, …).  
Suhu max/min / bola basah / visibility **tidak** di-fallback ke `t2m` (nilai palsu). UI menandai param yang tidak ada di NC.

Lead time dashboard: **D+0 … D+7** (0–168 jam, step 3 jam).

---

## Model & parameter

| Model | Status tipikal | Sumber NC |
|-------|----------------|-----------|
| **InaNWP** | real | litbangweb `monas_nc/*-asim.nc` (crop dari wrfout) |
| InaCAWO / GFS / IFS | sering `none` / dummy sampai NC ada | pattern di `backend/config.py` → `MODEL_LOCAL_PATHS` |

Parameter verify: `VERIFY_PARAMETERS` di `backend/config.py`  
(suhu 2m, Td, RH, QFF/QFE, angin, hujan 6h/24h, awan, …).

---

## UI (tab)

| Tab | Keterangan |
|-----|------------|
| Overview & Ranking | Ranking RMSE + KPI |
| Scores vs Lead Time | Metrik dropdown: RMSE / MAE / Bias / stde / r |
| Peta Stasiun | Canvas map + klik stasiun (fcst/obs/err) |
| Metode HARP | Dokumentasi metodologi |
| Detail Stasiun | Time series per-init; legend = nama model; zoom/pan |

**Lead time** (Overview & Peta): slider + **‹ / ▶ / ›** (prev / play / next).  
Play: maju tiap ~800 ms, loop; pause saat ganti tab / drag slider.

Frontend: `frontend/` · Canvas: chart + peta (Carto tiles; butuh `CARTO_API_KEY` di `.env` webpsi).

---

## Stack & path penting

| Layer | Teknologi / path |
|-------|------------------|
| API | FastAPI `backend/main.py` · port **8013** |
| Frontend static | `server-static.js` · port **3013** |
| PM2 | `ecosystem.config.cjs` → `monas-api`, `monas-web` |
| Compute | `docker/compute/` · `scripts/harp_compute.py` · `backend/services/harp_*.py` |
| webpsi path | `/var/www/monas` · URL `/monas/` |
| litbangweb NC | `/opt/lampp/htdocs/wrf/wrfout/` → crop → `.../monas_nc/` |
| litbangweb obs | `/opt/lampp/htdocs/wrf/monas_obs/` |
| litbangweb export | `/opt/lampp/htdocs/wrf/monas_export/` |

### Port (hindari bentrok di PSIMKG)

| App | FE | API |
|-----|----|-----|
| **MONAS** | **3013** | **8013** |
| PSIIDN | 3010 | 8010 |
| … | … | … |

---

## Deploy cepat

### webpsi (setelah `git pull`)

```bash
cd /var/www/monas
git pull
# pastikan .env: SERVE_READONLY=true, STORE_BACKEND=f32, BASE_PATH=/monas, CARTO_API_KEY=...
pm2 startOrReload ecosystem.config.cjs
pm2 restart monas-api --update-env
# hard-refresh browser
```

Pull artifact dari litbangweb (dari webpsi atau PC hub):

```bash
bash scripts/pull_artifacts_from_litbangweb.sh
pm2 restart monas-api
```

### litbangweb compute

Lihat `docs/DEPLOY_LITBANGWEB_WEBPSI.md` §C–D  
(crop CDO wajib; jangan HARP-kan wrfout 12GB).

### Dev lokal (PC)

```bash
cp .env.example .env   # BASE_PATH kosong, SERVE_READONLY=false untuk hitung lokal
python -m venv .venv && .venv/bin/pip install -r requirements.txt
./start.sh             # atau start.bat di Windows
```

→ http://localhost:3013

---

## API utama

Prefix produksi: `/monas/api/...` (Apache proxy). Dev: `/api/...`.

| Endpoint | Fungsi |
|----------|--------|
| `GET /api/health` | Mode readonly / store backend |
| `GET /api/parameters` | Meta + `available_by_model` + `unavailable_notes` |
| `GET /api/models/sources` | real / none / dummy per model |
| `GET /api/cycles` | Init cycles |
| `GET /api/verification/scores` | Skor vs lead (semua metrik) |
| `GET /api/verification/ranking` | Ranking |
| `GET /api/verification/map` · `.../map/bulk` | Peta stasiun |
| `GET /api/station/{id}/detail` | Time series stasiun |
| `GET /api/harp/methodology` | Teks metodologi |
| `POST /api/pipeline/run` | **Ditolak** jika `SERVE_READONLY` |

---

## Observasi BMKG

```
POST .../api/v21/user/session/login
POST .../api/v21/export/observation/by-station/query
     parameter_names: ["*"], chunk ≤ ~4 hari
```

Kredensial di `.env` (`BMKG_USERNAME` / `BMKG_PASSWORD`).  
Hanya dari **intranet BMKG** (Cloudflare memblokir publik).

Uji di webpsi (jika API reachable):

```bash
.venv/bin/python scripts/fetch_obs_local.py --test-api \
  --from 2026-09-01T00:00:00Z --to 2026-09-02T23:59:00Z
```

---

## Yang belum / backlog (untuk agent berikutnya)

- [ ] NC historis panjang (bulan–tahun) untuk verifikasi stabil + opsi **bias correction** (MBR/MOS) — data InaNWP baru masih pendek
- [ ] Model real InaCAWO / GFS / IFS (saat ini sering `none`)
- [ ] Plot N-cases vs lead (seperti harpR) di tab Scores
- [ ] Bias correction: per lead (± per stasiun) setelah archive cukup; jangan ML dulu

---

## Referensi dokumen

| File | Isi |
|------|-----|
| `AGENT_BRIEF.md` | Handoff singkat untuk agent baru |
| `docs/DEPLOY_LITBANGWEB_WEBPSI.md` | Arsitektur + cron + Docker + pull artifact |
| `docs/DAILY_PC_WEBPSI_FLOW.md` | Cadangan compute di PC |
| `docs/OBS_SYNC.md` | Sync observasi |
| `docs/DESIGN_NWP_VERIFICATION_DASHBOARD.md` | Desain / metodologi awal |
| `DEPLOY_MONAS.md` | Apache + PM2 di PSIMKG |
| `.env.example` | Semua env var |

---

*Update arsitektur: 2026-09-13 — PC obs → litbangweb Docker f32 → webpsi SERVE_READONLY.*
