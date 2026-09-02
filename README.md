# NWP Verification Dashboard (MONAS)

Dashboard **display-only** — user tidak perlu upload file model.
Model NC di-sync otomatis dari server **litbangweb** (`/opt/lampp/htdocs/wrf/wrfout/`).

**Produksi PSIMKG:** https://psimkg.bmkg.go.id/monas/ — lihat `DEPLOY_MONAS.md`.

## Arsitektur

```
litbangweb server
├── /opt/lampp/htdocs/wrf/wrfout/*.nc     ← model auto-update (InaNWP asim, dll)
├── /var/www/monas/                       ← deploy PSIMKG
└── pipeline (mingguan / cron):
    scan NC baru → interpolasi ke stasiun → HARP verify → simpan skor → tampil di UI
```

User hanya melihat hasil verifikasi HARP: **D+0 (analysis)** sampai **D+7** (168 jam).

## Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Python **FastAPI** (:8013) |
| Frontend | Static HTML/JS + **Plotly.js** + **Leaflet** (:3013) |
| Database | SQLite (`data/nwp_verify.db`) |
| Verifikasi | Python reimplementasi alur **harpPoint** / **harpIO** |

**Plot & tabel di browser = JavaScript (Plotly + HTML), bukan Python.** Python hanya menghitung skor saat pipeline dan melayani JSON via API. PSIIDN skew-T cepat karena pakai **HTML Canvas 2D** (React `SoundingSkewT`), bukan Plotly — stack berbeda, bukan backend Python.

## Implementasi MONAS (referensi internal)

| Komponen | Detail |
|----------|--------|
| Interpolasi | `RegularGridInterpolator` (Python/scipy) — setara harpIO `transformation=interpolate` |
| Verifikasi | `backend/services/verification.py` — `det_verify`, `common_cases`, `compute_ranking` |
| Lead time | 0–168 jam (D+0 … D+7), step 3 jam |
| Cache | `backend/services/verification_cache.py` |

## Ranking model (harpPoint det_summary)

Peringkat mengikuti **harpPoint `det_verify()`** untuk variabel kontinu (`thresholds=NULL`):

- Mean **bias, RMSE, MAE, stde** lintas semua parameter × lead time (D+0–D+7)
- Filter **init cycle** dari sidebar (kosong = init terbaru untuk detail stasiun; ranking agregat semua init)
- Urutan = **mean RMSE terendah** (#1 = terbaik)
- MONAS menambahkan **mean korelasi (Pearson)** sebagai pelengkap
- KPI di tab Overview mengikuti **parameter & lead time** sidebar

**HARP tidak punya skill score generik untuk variabel kontinu.** Skill (Heidke SS, Brier SS, dll.) hanya muncul jika verifikasi **kategorikal** dengan `thresholds=` pada `det_verify()` / `ens_verify()`.

| Konteks | Skor skill HARP |
|---------|-----------------|
| `det_verify()` + thresholds | heidke_skill_score, pierce_skill_score, kuiper_skill_score, odds_ratio_skill_score, equitable_threat_score, … |
| `ens_verify()` + thresholds | brier_skill_score (vs klimatologi), fair_brier_score, roc_area, CRPS, … |

## Cache & pipeline (pola PSIIDN)

Semua kalkulasi verifikasi dijalankan **saat pipeline** (bukan saat buka website). Hasil disimpan di SQLite:

| Tabel | Isi |
|-------|-----|
| `verification_scores` | Skor agregat per model × param × lead × init |
| `verification_station_scores` | Skor per stasiun (peta kinerja BMKG) |
| `ranking_cache` | Peringkat pre-compute |

Dashboard **hanya membaca cache**. Operasi mingguan = verifikasi **init cycle baru saja** (incremental), bukan hitung ulang sejarah.

- Backfill otomatis saat startup jika skor ada tapi cache belum terisi
- Backfill manual (sekali): `python scripts/rebuild_dashboard_cache.py`

## Retention (rencana produksi)

| Data | Retention |
|------|-----------|
| `verification_scores` | Permanent |
| `verification_station_scores` | Permanent (penting untuk kinerja stasiun) |
| `forecasts` mentah | 14–30 hari setelah verifikasi |
| `observations` | Rolling ~18 bulan |
| NC mirror di server | ~12 minggu hot; arsip penuh di litbangweb |

Estimasi DB: ~5 GB/tahun (dominan `verification_station_scores`).

## Deploy

| Target | Dokumen |
|--------|---------|
| PSIMKG | `DEPLOY_MONAS.md` |
| litbangweb | `deploy_litbangweb.sh` |
| PC dev lokal | `SETUP_LOCAL.md` |

## Port

| Service | Port |
|---------|------|
| Frontend | **3013** |
| API | **8013** |

## Kenapa Cloud Agent tidak bisa akses `C:\` lokal?

| Agent | Berjalan di | Akses file lokal |
|-------|-------------|------------------|
| **Cloud Agent** | VM Linux remote Cursor | Tidak bisa `C:\Users\...` |
| **Local Agent** | Komputer Anda langsung | Bisa akses file lokal |

## Fetch Observasi (BMKG API POST)

Token auto-refresh ~47 jam.

```
POST /api/v21/user/session/login
POST /api/v21/export/observation/by-station/query  (parameter_names: ["*"])
```

## API

- `GET /api/pipeline/status` — status auto-sync
- `GET /api/pipeline/inventory` — daftar NC di server
- `GET /api/cycles` — init cycles tersedia (D-0)
- `GET /api/verification/scores?init_time=...&lead_time=...`
- `GET /api/verification/ranking`
- `GET /api/verification/map?model=...&parameter=...&lead_time=...`
- `GET /api/station/{id}/detail?parameter=...&models=...&init_time=...&lead_time=...`

## Detail Stasiun — performa

Tab **Detail Stasiun** memanggil API JSON lalu render **Plotly.js** di browser. Lambat jika:

- Init cycle = "semua" → ribuan titik fcst dari banyak cycle (sekarang default ke **init terbaru**)
- Lead time tidak difilter → gunakan slider sidebar (API filter `lead_time`)

Optimasi: `scattergl` untuk dataset besar, downsample plot max ~400 titik, tabel tetap 20 baris terakhir.
