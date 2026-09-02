"""HARP point verification methodology — referensi harpPoint & harpIO."""

HARP_REFERENCES = [
    {
        "title": "harpPoint",
        "url": "https://github.com/harphub/harpPoint",
        "description": "Point verification for NWP forecasts (Harmonised Assessment of Reanalysis Products).",
    },
    {
        "title": "harpIO — dokumentasi",
        "url": "https://harphub.github.io/harpIO/",
        "description": "Read/transform forecast & obs; interpolasi grid → titik stasiun.",
    },
    {
        "title": "harpIO — Transformations",
        "url": "https://harphub.github.io/harpIO/articles/transformations.html",
        "description": "Interpolasi ke point, regrid, cross-section saat read_forecast().",
    },
]

HARP_WORKFLOW = [
    {"step": 1, "name": "Read FCST", "detail": "Baca NetCDF model (harpIO read_forecast), interpolasi grid → titik stasiun sinoptik."},
    {"step": 2, "name": "Read OBS", "detail": "Observasi sinoptik BMKG (POST export API v21), format titik per stasiun & valid time."},
    {"step": 3, "name": "Join", "detail": "Gabungkan fcst & obs pada station_id + valid_time (inner join, harpPoint join_to_fcst). Hanya pasangan lengkap yang dipakai."},
    {"step": 4, "name": "QC", "detail": "Buang outlier jika |error| > 4σ (check_obs_against_fcst)."},
    {"step": 5, "name": "det_verify", "detail": "Hitung skor deterministik per parameter & lead time (paired: fcst & obs harus ada)."},
    {"step": 6, "name": "common_cases", "detail": "Perbandingan adil antar model: hanya kasus yang ada di semua model (ranking)."},
]

HARP_SCORES = [
    {"id": "bias", "formula": "mean(fcst − obs)", "note": "Positive = model terlalu tinggi"},
    {"id": "rmse", "formula": "√mean((fcst − obs)²)", "note": "Metrik utama ranking"},
    {"id": "mae", "formula": "mean(|fcst − obs|)", "note": "Rata-rata absolute error"},
    {"id": "stde", "formula": "std(fcst − obs, ddof=1)", "note": "Spread error — bagian det_verify harpPoint"},
    {"id": "correlation", "formula": "Pearson(fcst, obs)", "note": "Korelasi linear — bagian det_verify harpPoint"},
]

HARP_RANKING = (
    "Peringkat mengikuti harpPoint det_verify() untuk variabel kontinu (thresholds=NULL): mean "
    "bias, RMSE, MAE, stde lintas semua parameter × lead time (D+0–D+7), filter init cycle sidebar. "
    "Urutan = mean RMSE terendah (#1). HARP tidak punya skill score generik untuk kontinu — skill "
    "(Heidke, Brier SS, dll.) hanya muncul jika verifikasi kategorikal dengan thresholds=. "
    "MONAS menambahkan mean korelasi (Pearson) sebagai pelengkap. KPI di tab Overview mengikuti "
    "parameter & lead time sidebar; ranking diagregasi dari cache SQLite (pre-compute saat pipeline, "
    "pola sama PSIIDN)."
)

HARP_SKILL_SCORES = [
    {
        "context": "det_verify() + thresholds (kategorikal)",
        "scores": "heidke_skill_score, pierce_skill_score, kuiper_skill_score, odds_ratio_skill_score, equitable_threat_score, …",
        "note": "Butuh argumen thresholds= pada det_verify(). Contoh: suhu ≥30°C, hujan ≥5 mm.",
    },
    {
        "context": "ens_verify() + thresholds (ensemble probabilistik)",
        "scores": "brier_skill_score (vs klimatologi observasi), fair_brier_score, roc_area, CRPS, …",
        "note": "Referensi default = sample climatology. MONAS saat ini fokus deterministik point (det_verify).",
    },
]

HARP_QC = (
    "Outlier dibuang jika |e| > 4σ. Arah angin: error melingkar (circular). "
    "Lead time D+0 (analysis) s/d D+7 (168 jam). "
    "Perhitungan paired: jika fcst atau obs hilang untuk (station, valid_time), baris itu tidak masuk skor."
)

HARP_PYTHON_NOTE = (
    "HARP resmi ditulis dalam R (harpPoint, harpIO). MONAS mengimplementasikan alur yang sama "
    "dalam Python: baca NetCDF → interpolasi scipy → SQLite → join paired → det_verify. "
    "Output skor (bias, RMSE, MAE, stde, correlation) setara harpPoint; format penyimpanan SQLite "
    "menggantikan workflow R→SQLite yang dipakai di lingkungan litbangweb."
)

HARP_CACHE = (
    "Pola PSIIDN: semua kalkulasi verifikasi dijalankan saat pipeline (bukan saat buka website). "
    "Hasil disimpan di SQLite — verification_scores (agregat), verification_station_scores (peta), "
    "ranking_cache (peringkat). Dashboard hanya membaca cache. Backfill otomatis saat startup jika "
    "skor ada tapi cache belum terisi; manual: python scripts/rebuild_dashboard_cache.py"
)


def get_methodology() -> dict:
    return {
        "title": "Metode HARP Point Verification",
        "subtitle": "Harmonised Assessment of Reanalysis Products — pola harpPoint / harpIO",
        "references": HARP_REFERENCES,
        "workflow": HARP_WORKFLOW,
        "scores": HARP_SCORES,
        "qc": HARP_QC,
        "ranking": HARP_RANKING,
        "skill_scores": HARP_SKILL_SCORES,
        "cache": HARP_CACHE,
        "python_equivalence": HARP_PYTHON_NOTE,
        "implementation": {
            "interpolation": "RegularGridInterpolator (Python/scipy) — setara harpIO transformation=interpolate",
            "verification": "backend/services/verification.py — det_verify, common_cases, compute_ranking",
            "lead_time": "0–168 jam (D+0 … D+7), step 3 jam",
        },
        "data_sources": {
            "InaNWP": "NetCDF real (*-asim.nc)",
            "InaCAWO": "Dummy (derived dari InaNWP) — NC belum tersedia",
            "GFS": "Dummy (derived dari InaNWP) — NC belum tersedia",
            "IFS": "Dummy (derived dari InaNWP) — NC belum tersedia",
        },
    }
