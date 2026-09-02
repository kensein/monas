# Agent Brief — NWP Verification Dashboard (MONAS)

> **Dokumen handoff** untuk Local Agent baru.  
> Rangkuman seluruh percakapan Cloud Agent + keputusan arsitektur + rencana deploy **webpsi**.

---

## 1. Tujuan Project

Dashboard interaktif **verifikasi Numerical Weather Prediction (NWP)** yang membandingkan **4 model**:

| Model | Keterangan |
|-------|------------|
| **InaNWP** | WRF asimilasi BMKG (file `*-asim.nc`) |
| **InaCAWO** | Coupled Atmosphere-Ocean-Wave |
| **GFS** | NOAA global model |
| **IFS** | ECMWF |

Data model diverifikasi terhadap **observasi stasiun sinoptik BMKG** (format titik, seluruh Indonesia) dengan metodologi **HARP** (Harmonised Assessment / harpPoint point verification).

**User tidak upload file model** — dashboard **display-only** menampilkan hasil verifikasi HARP dari D+0 (analysis) sampai D+7 (168 jam), termasuk **ranking model** (skill terbaik).

---

## 2. Timeline Keputusan (dari percakapan)

| Tahap | Keputusan |
|-------|-----------|
| Awal | Desain dashboard + formula HARP → `docs/DESIGN_NWP_VERIFICATION_DASHBOARD.md` |
| Port | Frontend **3013**, API **8013** |
| Model input | NetCDF, contoh: `2026070112-d01-asim.nc` (init 2026-07-01 12 UTC, domain d01, run asim) |
| Observasi | BMKG Sinoptik API v21 (POST) — semua parameter dari PPTX `API Export Sinoptik (search api)` |
| Upload NC | Ditolak untuk user — file 11–12 GB, tidak praktis via browser |
| litbangweb deploy | Awalnya direncanakan, **dibatalkan** — litbangweb **tidak punya akses internet** |
| Cloud Agent | Tidak bisa akses `C:\Users\husei\...` — hanya VM Linux remote |
| Opsi B (dipilih) | Develop & run di **PC BMKG lokal** + Cursor **Local Agent** |
| Deploy produksi | Server **PSIMKG** → `https://psimkg.bmkg.go.id/verifikasi-inanwp/` |

---

## 3. Arsitektur Final

```
┌─────────────────────────────────────────────────────────────────┐
│  SUMBER DATA                                                     │
├─────────────────────────────────────────────────────────────────┤
│  Model NC (*.nc)                                                 │
│    • litbangweb: /opt/lampp/htdocs/wrf/wrfout/  (SFTP internal)  │
│    • Lokal dev:  C:\nwp-data\models\{InaNWP,GFS,...}\           │
│    • Nanti webpsi: sync dari litbangweb (pola psiidn)            │
│                                                                  │
│  Observasi Sinoptik                                              │
│    • POST bmkgsatu.bmkg.go.id/api/v21/export/observation/...     │
│    • parameter_names: ["*"] — semua parameter PPTX slide 8       │
│    • Token auto-refresh ~47 jam (cache: data/cache/bmkg_token.json)│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PIPELINE (background, tiap 1 jam)                               │
│  scan NC → interpolasi grid→stasiun → join obs → HARP verify  │
│  → simpan skor ke SQLite → dashboard baca hasil saja             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  DASHBOARD (display-only)                                        │
│  • Ranking model (mean RMSE)                                     │
│  • Scores vs Lead Time D+0→D+7 (klik titik = angka detail)      │
│  • Peta stasiun interaktif                                       │
│  • Detail stasiun time series                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Metodologi HARP (Formula Inti)

Workflow: `Read FCST → Read OBS → Join → QC → common_cases → det_verify → Plot`

**Error:** \( e_i = f_i - o_i \)

| Skor | Formula |
|------|---------|
| Bias | mean(e) |
| RMSE | sqrt(mean(e²)) |
| MAE | mean(\|e\|) |
| STDE | std(e) |
| Correlation | Pearson(fcst, obs) |

**QC:** buang outlier jika \|e\| > 4σ  
**Fair compare:** `common_cases` — hanya kasus yang ada di semua model  
**Groupings:** default per `lead_time`; juga init cycle, region

Detail lengkap: `docs/DESIGN_NWP_VERIFICATION_DASHBOARD.md`

---

## 5. BMKG Sinoptik API (dari PPTX)

### Login (token 48 jam, auto-refresh di app)
```
POST https://bmkgsatu.bmkg.go.id/api/v21/user/session/login
Body: {"username": "...", "password": "..."}
```

### Export observasi (semua parameter)
```
POST https://bmkgsatu.bmkg.go.id/api/v21/export/observation/by-station/query
Header: Authorization: Bearer <token>
Body: {
  "data_type": "sinoptik",
  "parameter_names": ["*"],
  "station_wmo_ids": ["*"],
  "date_from": "2025-06-01T00:00:00Z",
  "date_to": "2025-06-03T23:59:00Z",
  "order_timestamp_code": 1
}
```
Chunk request **≤4 hari** per call (rekomendasi PPTX).

### Parameter verifikasi utama (numeric)
`temp_drybulb_c_tttttt`, `temp_dewpoint_c_tdtdtd`, `relative_humidity_pc`, `pressure_qff_mb_derived`, `wind_speed_ff`, `wind_dir_deg_dd`, `rainfall_6h_rrr`, `rainfall_24h_rrrr`, `cloud_cover_oktas_m`, `visibility_vv`, dll. — lihat `backend/config.py` → `VERIFY_PARAMETERS` dan `SINOPTIK_PARAMETERS`.

---

## 6. Infrastruktur Server

| Server | Role | Internet | Catatan |
|--------|------|----------|---------|
| **litbangweb** | Sumber NC + psiidn_export | ❌ Tidak | SFTP `202.90.199.54:3346`, path NC: `/opt/lampp/htdocs/wrf/wrfout/` |
| **PC BMKG (lokal)** | Dev + Local Agent | Intranet BMKG | Opsi B — `SETUP_LOCAL.md` |
| **webpsi** | Deploy produksi (rencana) | ✅ | Pola sama psiidn: SFTP pull dari litbangweb |

### File NC di litbangweb (sudah diverifikasi via SFTP)
```
/opt/lampp/htdocs/wrf/wrfout/
├── 2026070112-d01-asim.nc   (~12 GB)
├── 2026082500-d01-asim.nc
├── 2026083000-d01-asim.nc
└── 2026072700-d01-asim.nc
```

### SFTP litbangweb
- Host: `202.90.199.54`, Port: `3346`, User: `litbangweb`
- Password: lihat `.env` (jangan commit)

### Kredensial BMKG API
- User: `psimkg` — password di `.env`
- Hanya bisa diakses dari **intranet BMKG** (Cloudflare block dari internet publik)

---

## 7. Tech Stack & Repo

**GitHub:** `https://github.com/kensein/monas`  
**Branch aktif:** `cursor/nwp-verification-dashboard-design-51e8`  
**PR:** #1

```
monas/
├── backend/
│   ├── main.py                 # FastAPI :8013
│   ├── config.py               # Model paths, parameters, env
│   └── services/
│       ├── verification.py     # HARP det_verify, ranking
│       ├── nc_reader.py        # xarray NC → point interpolation
│       ├── pipeline.py         # Auto-scan NC, run verification
│       ├── bmkg_auth.py        # Token auto-refresh
│       ├── bmkg_export.py      # POST Sinoptik export
│       ├── obs_fetcher.py      # SQLite obs cache
│       ├── sftp_client.py      # List/sync NC dari litbangweb
│       └── scheduler.py        # Pipeline tiap 1 jam
├── frontend/
│   ├── index.html              # Dashboard UI
│   ├── app.js                  # Plotly + Leaflet interaktif
│   └── styles.css
├── data/                       # SQLite, cache (gitignored)
├── docs/DESIGN_NWP_VERIFICATION_DASHBOARD.md
├── SETUP_LOCAL.md              # Panduan Opsi B (PC lokal)
├── start.bat                   # Windows
├── start.sh                    # Linux
├── deploy_litbangweb.sh        # (tidak dipakai — no internet)
├── requirements.txt
└── .env.example
```

**Jalankan lokal:**
```cmd
copy .env.example .env
python -m pip install -r requirements.txt
start.bat          REM Windows
./start.sh         REM Linux
```
→ http://localhost:3013

---

## 8. API Endpoints Penting

| Endpoint | Fungsi |
|----------|--------|
| `GET /api/pipeline/status` | Status auto-sync |
| `GET /api/pipeline/inventory` | Daftar NC terdeteksi |
| `POST /api/pipeline/run` | Trigger pipeline manual |
| `GET /api/cycles` | Init cycles (D-0) tersedia |
| `GET /api/verification/ranking` | Ranking model |
| `GET /api/verification/scores` | Skor per parameter & lead time |
| `GET /api/verification/map` | Data peta stasiun |
| `GET /api/station/{id}/detail` | Time series stasiun |
| `GET /api/bmkg/token-status` | Status token API |

---

## 9. Konfigurasi `.env` (Local Agent)

```ini
BMKG_USERNAME=psimkg
BMKG_PASSWORD=<isi di .env>
BMKG_API_BASE=https://bmkgsatu.bmkg.go.id

INANWP_NC_PATH=C:\nwp-data\models\InaNWP
INACAWO_NC_PATH=C:\nwp-data\models\InaCAWO
GFS_NC_PATH=C:\nwp-data\models\GFS
IFS_NC_PATH=C:\nwp-data\models\IFS

FORCE_PIPELINE=true
API_PORT=8013
FRONTEND_PORT=3013

SFTP_HOST=202.90.199.54
SFTP_PORT=3346
SFTP_USER=litbangweb
SFTP_PASSWORD=<isi di .env>
```

---

## 10. Instruksi untuk Local Agent Baru

1. **Buka Cursor Desktop** (bukan Cloud Agent)
2. **Open Folder:** clone `monas`, checkout branch `cursor/nwp-verification-dashboard-design-51e8`
3. **Mode Agent** — jangan pilih "Run in cloud"
4. Ikuti `SETUP_LOCAL.md` untuk setup PC BMKG
5. Copy NC dari litbangweb via FileZilla → `C:\nwp-data\models\InaNWP\`
6. Jalankan `start.bat`, buka http://localhost:3013
7. Verifikasi pipeline: `GET http://localhost:8013/api/pipeline/inventory`

**Prompt awal yang disarankan untuk Local Agent:**
> Lanjutkan development dashboard verifikasi NWP di repo monas. Baca AGENT_BRIEF.md dan SETUP_LOCAL.md. Target deploy produksi nanti di webpsi. Fokus: pipeline NC 4 model, observasi BMKG API, ranking HARP D+0–D+7.

---

## 11. Deploy PSIMKG (portal sub-app)

| Item | Nilai |
|------|-------|
| Deploy path | `/var/www/verifikasi-inanwp` |
| URL publik | https://psimkg.bmkg.go.id/verifikasi-inanwp/ |
| Apache snippet | `deploy/apache-verifikasi-inanwp.conf` |
| PM2 | `ecosystem.config.cjs` |
| Panduan | `DEPLOY_PSIMKG.md`, `SETUP_LOCAL.md` |

Dev lokal dulu di PC BMKG → `git push` → `git pull` di server → `./deploy_psimkg.sh`

---

## 12. Yang Sudah Selesai vs Belum

### ✅ Selesai (Cloud Agent)
- Desain HARP lengkap + dokumentasi
- Backend FastAPI + pipeline auto-sync
- Frontend interaktif (ranking, scores, peta, stasiun)
- BMKG API login + token auto-refresh + POST export
- SFTP inventory litbangweb wrfout
- Windows `start.bat` + `SETUP_LOCAL.md`
- SQLite cache obs + verification scores

### ⏳ Belum / Perlu Local Agent
- [ ] Test end-to-end dengan NC 12GB asli di PC lokal (`scripts/test_nc_pipeline.py --full`)
- [ ] Test BMKG API dari intranet BMKG (login psimkg)
- [x] Deploy script Apache + PM2 untuk **webpsi**
- [x] Sync otomatis NC litbangweb → webpsi (script + cron)
- [ ] Model InaCAWO, GFS, IFS — pastikan file NC & naming ada di wrfout
- [ ] Production hardening (logging, auth)

---

## 13. Konteks psiidn vs Agent Cloud vs Local

| | Cloud Agent | Local Agent | psiidn |
|---|-------------|-------------|--------|
| Akses C:\ lokal | ❌ | ✅ | ✅ (di server) |
| Akses litbangweb SFTP | ✅ | ✅ (intranet) | ✅ |
| BMKG API intranet | ❌ (Cloudflare) | ✅ | ✅ |
| Deploy webpsi | ❌ | ✅ (user deploy) | ✅ (sudah jalan) |

**Alasan pilih Local Agent:** litbangweb tanpa internet; file NC 12GB di wrfout; BMKG API hanya intranet; deploy akhir di webpsi.

---

## 14. File Referensi User

- PPTX: `API Export Sinoptik (search api).pptx` — parameter & endpoint API
- NC sample: `2026070112-d01-asim.nc` (user: `C:\Users\husei\Downloads\` atau litbangweb wrfout)
- SFTP config: FileZilla ke litbangweb (gambar user di percakapan awal)

---

*Dibuat: 2026-09-02 — handoff Cloud Agent → Local Agent → deploy webpsi*
