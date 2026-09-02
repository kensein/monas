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
    {"step": 3, "name": "Join", "detail": "Gabungkan fcst & obs pada station_id + valid_time (inner join, harpPoint join_to_fcst)."},
    {"step": 4, "name": "QC", "detail": "Buang outlier jika |error| > 4σ (check_obs_against_fcst)."},
    {"step": 5, "name": "det_verify", "detail": "Hitung skor deterministik per parameter & lead time."},
    {"step": 6, "name": "common_cases", "detail": "Perbandingan adil: hanya kasus yang ada di semua model (ranking)."},
]

HARP_SCORES = [
    {"id": "bias", "formula": "mean(fcst − obs)", "note": "Positive = model terlalu tinggi"},
    {"id": "rmse", "formula": "√mean((fcst − obs)²)", "note": "Metrik utama ranking"},
    {"id": "mae", "formula": "mean(|fcst − obs|)", "note": "Rata-rata absolute error"},
    {"id": "stde", "formula": "std(fcst − obs)", "note": "Spread error"},
    {"id": "correlation", "formula": "Pearson(fcst, obs)", "note": "Korelasi linear"},
]

HARP_QC = "Outlier dibuang jika |e| > 4σ. Arah angin: error melingkar (circular). Lead time D+0 (analysis) s/d D+7 (168 jam)."


def get_methodology() -> dict:
    return {
        "title": "Metode HARP Point Verification",
        "subtitle": "Harmonised Assessment of Reanalysis Products — pola harpPoint / harpIO",
        "references": HARP_REFERENCES,
        "workflow": HARP_WORKFLOW,
        "scores": HARP_SCORES,
        "qc": HARP_QC,
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
