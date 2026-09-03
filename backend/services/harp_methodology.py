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

HARP_QC = (
    "Outlier dibuang jika |e| > 4σ. Arah angin: error melingkar (circular). "
    "Lead time D+0 (analysis) s/d D+7 (168 jam). "
    "Perhitungan paired: jika fcst atau obs hilang untuk (station, valid_time), baris itu tidak masuk skor."
)

HARP_PYTHON_NOTE = (
    "HARP resmi ditulis dalam R (harpPoint, harpIO). MONAS mengimplementasikan alur yang sama "
    "dalam Python: NetCDF (crop 2D) → interpolasi scipy ke titik stasiun → join paired dengan obs "
    "→ det_verify. Output skor (bias, RMSE, MAE, stde, correlation) setara harpPoint. "
    "Hasil disimpan sebagai array float32 + JSON (pola PSIIDN) yang disinkron ke webpsi, "
    "sehingga dashboard hanya membaca file, tidak menghitung ulang."
)

def get_methodology() -> dict:
    return {
        "title": "Metode HARP Point Verification",
        "subtitle": "Harmonised Assessment of Reanalysis Products — pola harpPoint / harpIO",
        "references": HARP_REFERENCES,
        "workflow": HARP_WORKFLOW,
        "scores": HARP_SCORES,
        "qc": HARP_QC,
        "python_equivalence": HARP_PYTHON_NOTE,
        "data_sources": {
            "InaNWP": "NetCDF real (*-asim.nc, crop 2D via CDO/ncks)",
            "InaCAWO": "NC belum tersedia",
            "GFS": "NC belum tersedia",
            "IFS": "NC belum tersedia",
        },
    }
