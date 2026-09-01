# Desain Dashboard Verifikasi NWP Multi-Model (HARP-based)

> Verifikasi titik (point verification) untuk **InaNWP**, **InaCAWO**, **GFS**, dan **IFS**  
> terhadap observasi stasiun sinoptik BMKG seluruh Indonesia.

---

## 1. Tujuan & Ruang Lingkup

Dashboard interaktif untuk:

1. Membandingkan 4 model NWP dengan observasi stasiun BMKG (format titik).
2. Menghitung skor verifikasi deterministik mengikuti metodologi **HARP** (Harmonised Assessment of Reanalysis Products / harpPoint).
3. Menampilkan hasil per parameter, lead time, periode, dan wilayah.
4. Plot interaktif: klik titik/stasiun → tampil angka model, observasi, dan error.

**Di luar ruang lingkup fase 1:** verifikasi ensemble (kecuali InaNWP punya ensemble), verifikasi grid-to-grid.

---

## 2. Arsitektur Sistem

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React + Plotly/MapLibre)              │
│  Filter │ Peta Stasiun │ Score Charts │ Taylor Diagram │ Detail Panel   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ REST API / WebSocket
┌───────────────────────────────▼─────────────────────────────────────────┐
│                    BACKEND (Python FastAPI / Node)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ NC Reader    │  │ Obs Fetcher  │  │ Interpolator │  │ HARP Verify │ │
│  │ (xarray)     │  │ (SFTP/API)   │  │ (grid→point) │  │ Engine      │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
  Model NC Files          Obs Cache DB            Precomputed Scores
  (local/upload)     (Postgres/SQLite)         (Parquet/Redis cache)
        │                       │
        │                       ▼
        │              SFTP litbangweb
        │              puslitbang:/opt/lampp/htdocs/monas
        │              + API Export Sinoptik (search)
        ▼
  InaNWP / InaCAWO / GFS / IFS
  *.nc  (contoh: 2026070112-d01-asim.nc)
```

---

## 3. Workflow HARP (Inti Metodologi)

Mengikuti alur resmi HARP point verification:

```
READ FCST → READ OBS → JOIN → QC → COMMON_CASES → VERIFY → SAVE/PLOT
```

| Langkah | Fungsi HARP | Implementasi Dashboard |
|---------|-------------|------------------------|
| 1. Read forecast | `read_point_forecast()` | Parse NC → interpolasi ke koordinat stasiun |
| 2. Read obs | `read_point_obs()` | Fetch API Sinoptik / cache SFTP |
| 3. Join | `join_to_fcst()` | Inner join: `(station_id, valid_time, parameter)` |
| 4. QC | `check_obs_against_fcst()` | Buang outlier > N×σ dari selisih fcst−obs |
| 5. Fair compare | `common_cases()` | Hanya kasus yang ada di **semua 4 model** |
| 6. Verify | `det_verify()` | Hitung bias, RMSE, MAE, stde, skor kategorikal |
| 7. Plot | `plot_point_verif()` | Line chart, hexbin, peta choropleth |

---

## 4. Formula Verifikasi (Deterministik)

### 4.1 Notasi

- \(f_i\) = nilai prakiraan model ke-\(i\)
- \(o_i\) = nilai observasi ke-\(i\)
- \(e_i = f_i - o_i\) = error (selisih prakiraan − observasi)
- \(N\) = jumlah pasangan fcst–obs setelah QC dan `common_cases`

### 4.2 Summary Scores (HARP `det_verify`)

| Skor | Formula | Satuan | Interpretasi |
|------|---------|--------|--------------|
| **Bias** | \(\bar{e} = \frac{1}{N}\sum_{i=1}^{N}(f_i - o_i)\) | sama dgn parameter | Positif = model terlalu tinggi |
| **RMSE** | \(\sqrt{\frac{1}{N}\sum_{i=1}^{N}(f_i - o_i)^2}\) | sama dgn parameter | Semakin kecil semakin baik |
| **MAE** | \(\frac{1}{N}\sum_{i=1}^{N}|f_i - o_i|\) | sama dgn parameter | Robust terhadap outlier |
| **STDE** | \(\sqrt{\frac{1}{N-1}\sum_{i=1}^{N}(e_i - \bar{e})^2}\) | sama dgn parameter | Variabilitas error |
| **Correlation (r)** | \(\frac{\sum(f_i-\bar{f})(o_i-\bar{o})}{\sqrt{\sum(f_i-\bar{f})^2\sum(o_i-\bar{o})^2}}\) | adimensional | 1 = sempurna |

### 4.3 Skor Kategorikal (dengan threshold)

Untuk parameter seperti curah hujan, suhu ekstrem, kecepatan angin kencang:

**Contingency table** (observed vs forecast, threshold \(T\)):

| | Obs ≥ T | Obs < T |
|---|---------|---------|
| **Fcst ≥ T** | Hit (H) | False Alarm (FA) |
| **Fcst < T** | Miss (M) | Correct Negative (CN) |

| Skor | Formula |
|------|---------|
| **Threat Score (TS)** | \(\frac{H}{H + M + FA}\) |
| **Hit Rate (POD)** | \(\frac{H}{H + M}\) |
| **False Alarm Rate** | \(\frac{FA}{H + FA}\) |
| **Frequency Bias** | \(\frac{H + FA}{H + M}\) |
| **Heidke Skill Score** | \(\frac{2(H \cdot CN - M \cdot FA)}{(H+M)(M+CN) + (H+FA)(FA+CN)}\) |
| **Equitable Threat Score** | TS dikoreksi random hit |

Threshold contoh:
- **T2m**: 28, 30, 32, 35 °C
- **RR 6h**: 1, 5, 10, 20, 50 mm
- **WS10m**: 10, 15, 20, 25 kt (≈ 5.1, 7.7, 10.3, 12.9 m/s)

### 4.4 Pengelompokan (Groupings)

Default HARP: **`lead_time`**. Tambahan untuk dashboard:

- `fcst_cycle` (00/06/12/18 UTC)
- `valid_time` (jam valid)
- `region` (5 Balai Wilayah BMKG)
- `station_type` (basic / non-basic)
- `season` (DJF, MAM, JJA, SON)

### 4.5 Quality Control

```python
# HARP-style: check_obs_against_fcst
# Buang obs jika |f_i - o_i| > num_sd_allowed × σ_pool
# Default num_sd_allowed = 4 (konfigurable per parameter)
```

### 4.6 Interpolasi Grid → Titik Stasiun

Untuk setiap stasiun \((lat_s, lon_s)\) dan valid time \(t\):

1. Ambil field model \(F(x,y,t)\) pada lead time yang sesuai.
2. Interpolasi **bilinear** (default HARP/harpIO) atau nearest-neighbor untuk orografi.
3. Konversi satuan ke standar observasi (lihat §5).

**Matching waktu:**
- `valid_time = init_time + lead_time`
- Observasi sinoptik jam utama: 00, 06, 12, 18 UTC (±30 menit toleransi)
- Observasi intermediate: 03, 09, 15, 21 UTC (opsional)

---

## 5. Mapping Parameter Model ↔ Observasi Sinoptik

### 5.1 Parameter Observasi (WMO FM-12 / API Export Sinoptik)

Ambil **semua parameter** dari API Export Sinoptik. Berdasarkan standar SYNOP BMKG, minimal:

| Kode API/Obs | Deskripsi | Satuan Obs | Interval |
|--------------|-----------|------------|----------|
| `TT` / `temp` | Suhu udara 2m | °C | 3/6 jam |
| `Td` / `dewpoint` | Titik embun | °C | 3/6 jam |
| `RH` | Kelembapan relatif | % | 3/6 jam |
| `PPPP` / `mslp` | Tekanan permukaan laut | hPa | 3/6 jam |
| `PP` / `station_pressure` | Tekanan stasiun | hPa | 3/6 jam |
| `ff` / `wind_speed` | Kecepatan angin | m/s atau kt | 3/6 jam |
| `dd` / `wind_dir` | Arah angin | derajat | 3/6 jam |
| `RRR` / `rain_6h` | Curah hujan 6 jam | mm | 6 jam |
| `RRR_24h` | Curah hujan 24 jam | mm | 24 jam |
| `N` / `tcc` | Tutupan awan total | okta (0–8) | 3/6 jam |
| `VV` | Jarak pandang | km | 3/6 jam |
| `Tx` / `Tn` | Suhu max/min harian | °C | harian |
| `ww` | Cuaca saat obs | kode WMO | 3/6 jam |
| `W1,W2` | Cuaca masa lalu | kode WMO | 3/6 jam |

> **Catatan:** Nama field exact mengikuti response API dari dokumen `API Export Sinoptik (search api).pptx`. Layer adapter normalisasi ke skema internal.

### 5.2 Mapping Variabel NetCDF per Model

| Parameter Verifikasi | InaNWP (WRF) | InaCAWO (WRF) | GFS | IFS (ECMWF) |
|---------------------|--------------|---------------|-----|-------------|
| T2m | `T2` (K→°C) | `T2` | `t2m` | `t2m` |
| RH2m | dari `T2`,`Q2` | dari `T2`,`Q2` | `rh2m` | `r` @1000hPa |
| Q2 / humidity | `Q2` | `Q2` | `sh2` | `q` |
| MSLP | `PSFC` atau diag | `PSFC` | `prmsl` | `msl` |
| U10, V10 | `U10`,`V10` | `U10`,`V10` | `u10`,`v10` | `u10`,`v10` |
| WS10m | \(\sqrt{U10^2+V10^2}\) | sama | sama | sama |
| WD10m | `atan2(U10,V10)` | sama | sama | sama |
| RR accum | `RAINNC` diff | `RAINNC` diff | `tp` diff | `tp` diff |
| TCC | `CLDFRA` integral / `TCDC` | sama | `tcc` | `tcc` |

**Konversi satuan:**
- Temperatur: K → °C (\(T°C = T_K - 273.15\))
- Tekanan: Pa → hPa jika perlu
- Angin: m/s ↔ kt (×1.94384)
- Curah hujan: akumulasi model di-difference ke interval obs (6h/24h)

### 5.3 Parsing Nama File NC

Contoh: `2026070112-d01-asim.nc`

| Segmen | Arti |
|--------|------|
| `2026070112` | Init time: 2026-07-01 12:00 UTC |
| `d01` | Domain 1 |
| `asim` | Run asimilasi (vs `noasim`) |

---

## 6. Sumber Data Observasi

### 6.1 Server litbangweb (SFTP)

```
Host: 202.90.199.54
Port: 3346
User: litbangweb
Path: /opt/lampp/htdocs/monas
```

**Strategi:**
1. **Batch sync** harian via SFTP → simpan ke DB lokal (PostgreSQL/SQLite).
2. Struktur cache: `obs/{YYYY}/{MM}/{DD}/{station_id}.json`
3. Metadata stasiun: WMO ID, nama, lat, lon, elev, BW, tipe (basic/non-basic).

### 6.2 API Export Sinoptik (Search API)

Endpoint & parameter query mengikuti PPTX. Pola umum:

```
GET /api/sinoptik/search?station={wmo_id}&start={ISO}&end={ISO}&params=all
```

Adapter layer:
- Fetch semua parameter yang tersedia
- Normalisasi ke schema `harp_obs`: `(station_id, valid_time, parameter, value, unit, qc_flag)`

---

## 7. Desain UI Dashboard

### 7.1 Layout Halaman

```
┌──────────────────────────────────────────────────────────────────────────┐
│  🌐 NWP Verification Dashboard — BMKG Litbang                            │
├──────────────┬───────────────────────────────────────────────────────────┤
│   SIDEBAR    │                    MAIN CONTENT                          │
│              │                                                           │
│ 📅 Periode   │  ┌─────────────────────────────────────────────────────┐ │
│ 🕐 Cycle     │  │  TAB: Overview │ Scores │ Map │ Station │ Compare  │ │
│ ⏱ Lead time  │  └─────────────────────────────────────────────────────┘ │
│ 📊 Parameter │                                                           │
│ 🗺 Region    │  [ Konten tab aktif ]                                     │
│ ✅ Models    │                                                           │
│   ☑ InaNWP   │                                                           │
│   ☑ InaCAWO  │                                                           │
│   ☑ GFS      │                                                           │
│   ☑ IFS      │                                                           │
│ ⚙ QC / Thr.  │                                                           │
└──────────────┴───────────────────────────────────────────────────────────┘
```

### 7.2 Tab Overview

**KPI Cards** (4 model, parameter terpilih, lead time terpilih):

| InaNWP | InaCAWO | GFS | IFS |
|--------|---------|-----|-----|
| RMSE: 1.2°C | RMSE: 1.4°C | RMSE: 1.8°C | RMSE: 1.5°C |
| Bias: +0.3°C | Bias: +0.1°C | ... | ... |

**Ranking bar chart** — model terbaik per skor (RMSE terendah = rank 1).

### 7.3 Tab Scores (Interaktif)

**Plot 1: Score vs Lead Time** (multi-line, 4 model)
- X: lead time (0–72/120 jam)
- Y: RMSE / MAE / Bias / r
- **Klik titik** → popup: `{model, lead_time, score, N_cases, N_stations}`

**Plot 2: Taylor Diagram**
- 4 model dalam 1 diagram per parameter
- Klik legenda → highlight model

**Plot 3: Hexbin / Scatter Fcst vs Obs**
- Per model, per lead time
- Klik bin → daftar stasion kontributor + nilai

**Plot 4: Box plot error per region**

### 7.4 Tab Map (Peta Stasiun — Interaktif)

```
        Peta Indonesia (MapLibre/Leaflet)
        ● warna = RMSE stasiun (parameter & LT terpilih)
        ● ukuran = jumlah kasus

        Klik stasiun → Panel kanan:
        ┌─────────────────────────────┐
        │ Stasiun: 96745 - Citeko     │
        │ Lat/Lon: -6.72, 106.85      │
        │ Parameter: T2m, LT: +12h    │
        ├─────────────────────────────┤
        │ Model  │ Fcst │ Obs │ Err  │
        │ InaNWP │ 28.3 │27.1 │ +1.2 │
        │ InaCAWO│ 28.0 │27.1 │ +0.9 │
        │ GFS    │ 29.1 │27.1 │ +2.0 │
        │ IFS    │ 27.8 │27.1 │ +0.7 │
        ├─────────────────────────────┤
        │ Mini time series (7 hari)   │
        │ [sparkline fcst vs obs]     │
        └─────────────────────────────┘
```

**Choropleth per provinsi** (opsional): RMSE rata-rata semua stasiun.

### 7.5 Tab Station Detail

- Dropdown pilih stasiun / search by WMO ID
- Time series interaktif: obs (titik) + 4 model (garis, warna berbeda)
- Brush/zoom periode
- Export CSV

### 7.6 Tab Compare

- **Multi-model difference map**: InaNWP − GFS, dll.
- **Skill score relative to baseline** (e.g., skill vs GFS)
- Tabel peringkat per BW (Medan, Jakarta, Denpasar, Makassar, Jayapura)

---

## 8. Interaktivitas Plot (Spesifikasi Teknis)

| Aksi User | Respons |
|-----------|---------|
| Klik titik di line chart | Tooltip + panel detail angka |
| Klik stasiun di peta | Popup tabel fcst/obs/error 4 model |
| Hover hexbin | Count, mean fcst, mean obs |
| Brush time series | Filter periode global |
| Double-click stasiun | Drill-down ke raw cases |
| Toggle model | Show/hide series |
| Slider lead time | Update semua panel sync |

**Library rekomendasi:** Plotly.js (click/hover events), MapLibre GL JS (geo layer).

---

## 9. Tech Stack

| Layer | Pilihan | Alasan |
|-------|---------|--------|
| Frontend | React + TypeScript | Komponen reusable, ecosystem chart |
| Charts | Plotly.js / ECharts | Klik → callback dengan data numerik |
| Map | MapLibre GL JS | Open source, performa baik |
| Backend | Python FastAPI | xarray/netCDF4, numpy, scipy |
| NC I/O | xarray + cfgrib (GFS/IFS) | Standard NWP |
| Interpolasi | scipy.interpolate.RegularGridInterpolator | Bilinear grid→point |
| Obs cache | PostgreSQL + PostGIS | Query spasial stasiun |
| Precompute | Celery + Redis | Batch verifikasi harian |
| Deploy | Docker on puslitbang / litbangweb | Dekat data obs |

---

## 10. Skema Database

### `stations`
```sql
station_id, wmo_id, name, lat, lon, elevation, region, is_basic
```

### `observations`
```sql
station_id, valid_time, parameter, value, unit, source, qc_flag
PRIMARY KEY (station_id, valid_time, parameter)
```

### `forecasts_point`
```sql
model, init_time, lead_time, station_id, parameter, value
PRIMARY KEY (model, init_time, lead_time, station_id, parameter)
```

### `verification_scores`
```sql
model, parameter, init_time_range, lead_time, grouping, score_name, score_value, n_cases, n_stations
```

---

## 11. Pipeline Batch Harian

```
06:00 UTC  Sync obs SFTP/API (jam 00 UTC obs)
06:30 UTC  Ingest NC model runs (00 UTC cycle)
07:00 UTC  Interpolate + join + QC
07:30 UTC  det_verify per parameter × model
08:00 UTC  Update dashboard cache
```

---

## 12. Fase Implementasi

| Fase | Deliverable | Prioritas |
|------|-------------|-----------|
| **F1** | Obs adapter (API Sinoptik) + stasiun metadata | Tinggi |
| **F2** | NC reader + interpolator (InaNWP dulu) | Tinggi |
| **F3** | HARP verify engine (bias, RMSE, MAE) | Tinggi |
| **F4** | Dashboard MVP: Map + Score vs LT | Tinggi |
| **F5** | 4 model + common_cases + Taylor diagram | Sedang |
| **F6** | Threshold scores (hujan, suhu ekstrem) | Sedang |
| **F7** | Export laporan PDF/CSV | Rendah |

---

## 13. Contoh API Backend

```
GET  /api/stations?region=Jakarta
GET  /api/verification/scores?models=InaNWP,GFS&parameter=T2m&start=...&end=...
GET  /api/verification/map?model=InaNWP&parameter=T2m&lead_time=12&score=rmse
GET  /api/station/{id}/timeseries?parameter=T2m&models=all&start=...&end=...
POST /api/verification/run  (trigger batch)
POST /api/models/upload-nc   (upload file NC)
```

---

## 14. Referensi

- HARP Point Verification: https://harphub.github.io/harp_training_2024/point-verif-workflow.html
- harpPoint `det_verify`: bias, rmse, mae, stde, threshold scores
- WMO FM-12 SYNOP code
- BMKG WIS2 surface observations (alternatif obs): wis2node.bmkg.go.id
