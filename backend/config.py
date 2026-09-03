import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
NC_DIR = DATA_DIR / "nc"
OBS_DIR = DATA_DIR / "obs"
CACHE_DIR = DATA_DIR / "cache"
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", str(DATA_DIR / "artifacts")))
DB_PATH = DATA_DIR / "nwp_verify.db"

# Folder export observasi di PC lokal (download BMKG API → SCP ke litbangweb)
OBS_EXPORT_DIR = os.getenv(
    "OBS_EXPORT_DIR",
    str(Path("D:/nwp-data/obs") if os.name == "nt" else DATA_DIR / "obs_export"),
)

# Rentang default fetch observasi (--from-june): 1 Juni tahun berjalan → sekarang
OBS_FETCH_START = os.getenv("OBS_FETCH_START", "").strip() or None

for d in (NC_DIR, OBS_DIR, CACHE_DIR, ARTIFACTS_DIR, Path(OBS_EXPORT_DIR)):
    d.mkdir(parents=True, exist_ok=True)

API_PORT = int(os.getenv("API_PORT", "8013"))
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "3013"))
API_HOST = os.getenv("API_HOST", os.getenv("HOST", "127.0.0.1"))
FRONTEND_HOST = os.getenv("FRONTEND_HOST", os.getenv("HOSTNAME", "127.0.0.1"))

# Subpath deploy di portal PSIMKG: https://psimkg.bmkg.go.id/monas/
BASE_PATH = os.getenv("BASE_PATH", "").rstrip("/")  # kosong = dev lokal (root)
CORS_ORIGIN = os.getenv("CORS_ORIGIN", "https://psimkg.bmkg.go.id")
DEPLOY_PATH = os.getenv("DEPLOY_PATH", "/var/www/monas")

# Production vs dev: set SEED_DEMO_DATA=false on webpsi / local with real NC
SEED_DEMO_DATA = os.getenv("SEED_DEMO_DATA", "true").lower() in ("1", "true", "yes")

# Single NC file override (Opsi B: C:\Users\...\2026070112-d01-asim.nc)
LOCAL_NC_PATH = os.getenv("LOCAL_NC_PATH", "").strip().strip('"').strip("'")

# Katalog stasiun BMKG (WMO + lat/lon) — default: data/stations_bmkg.json
STATION_CATALOG_PATH = os.getenv("STATION_CATALOG_PATH", "")
CARTO_API_KEY = os.getenv("CARTO_API_KEY", os.getenv("NEXT_PUBLIC_CARTO_API_KEY", ""))
NC_CHUNK_THRESHOLD_BYTES = int(os.getenv("NC_CHUNK_THRESHOLD_BYTES", str(500_000_000)))

# BMKG Sinoptik API (v21)
BMKG_API_BASE = os.getenv("BMKG_API_BASE", "https://bmkgsatu.bmkg.go.id")
BMKG_USERNAME = os.getenv("BMKG_USERNAME", "")
BMKG_PASSWORD = os.getenv("BMKG_PASSWORD", "")

# SFTP litbangweb
SFTP_HOST = os.getenv("SFTP_HOST", "202.90.199.54")
SFTP_PORT = int(os.getenv("SFTP_PORT", "3346"))
SFTP_USER = os.getenv("SFTP_USER", "litbangweb")
SFTP_PASSWORD = os.getenv("SFTP_PASSWORD", "_Pusl1tb4ng.123_")
SFTP_OBS_PATH = os.getenv("SFTP_OBS_PATH", "/opt/lampp/htdocs/wrf/monas_obs")

# litbangweb: baca obs dari folder JSON (tanpa BMKG API — server tanpa internet)
LITBANGWEB_OBS_DIR = os.getenv("LITBANGWEB_OBS_DIR", SFTP_OBS_PATH)
OFFLINE_OBS_MODE = os.getenv("OFFLINE_OBS_MODE", "false").lower() in ("1", "true", "yes")

# Model NC paths on litbangweb server (auto-sync, NO user upload)
# Default InaNWP: hasil crop CDO 2D (`scripts/crop_inanwp_cdo.sh`), bukan wrfout 12GB
MODEL_LOCAL_PATHS = {
    "InaNWP": {
        "path": os.getenv("INANWP_NC_PATH", "/opt/lampp/htdocs/wrf/monas_nc"),
        "pattern": "*-asim.nc",
    },
    "InaCAWO": {
        "path": os.getenv("INACAWO_NC_PATH", "/opt/lampp/htdocs/wrf/wrfout"),
        "pattern": "*-cawo.nc",
    },
    "GFS": {
        "path": os.getenv("GFS_NC_PATH", "/opt/lampp/htdocs/wrf/wrfout"),
        "pattern": "*-gfs.nc",
    },
    "IFS": {
        "path": os.getenv("IFS_NC_PATH", "/opt/lampp/htdocs/wrf/wrfout"),
        "pattern": "*-ifs.nc",
    },
}

# Max forecast lead time for dashboard (hours) — D+0 to D+7
MAX_LEAD_TIME_HOURS = int(os.getenv("MAX_LEAD_TIME_HOURS", "168"))
LEAD_TIME_STEP = int(os.getenv("LEAD_TIME_STEP", "3"))

# Auto pipeline interval (seconds)
PIPELINE_INTERVAL_SEC = int(os.getenv("PIPELINE_INTERVAL_SEC", "3600"))

# PC dev (Windows): jangan SFTP ke litbangweb saat startup — set false jika perlu inventory remote
_disable_sftp_env = os.getenv("DISABLE_SFTP")
if _disable_sftp_env is not None:
    DISABLE_SFTP = _disable_sftp_env.lower() in ("1", "true", "yes")
else:
    DISABLE_SFTP = os.name == "nt"

# Scheduler background pipeline (matikan di PC dev: ENABLE_PIPELINE_SCHEDULER=false)
ENABLE_PIPELINE_SCHEDULER = os.getenv("ENABLE_PIPELINE_SCHEDULER", "true").lower() in ("1", "true", "yes")

# webpsi/PSIMKG serve-only: jangan hitung ulang / jangan fetch NC / obs
SERVE_READONLY = os.getenv("SERVE_READONLY", "false").lower() in ("1", "true", "yes")

# Parallel verify (PC/HPC): proses beberapa model sekaligus
PARALLEL_VERIFY = os.getenv("PARALLEL_VERIFY", "true").lower() in ("1", "true", "yes")
PARALLEL_WORKERS = int(os.getenv("PARALLEL_WORKERS", "4"))
# process = ProcessPoolExecutor (HPC); thread = ThreadPoolExecutor (aman SQLite Windows)
PARALLEL_BACKEND = os.getenv("PARALLEL_BACKEND", "process" if os.name != "nt" else "thread").lower()

# Sync artifact ke webpsi (PC daily job)
WEBPSI_HOST = os.getenv("WEBPSI_HOST", "")
WEBPSI_USER = os.getenv("WEBPSI_USER", "")
WEBPSI_PATH = os.getenv("WEBPSI_PATH", "/var/www/monas/data/artifacts")
WEBPSI_SSH_PORT = int(os.getenv("WEBPSI_SSH_PORT", "22"))

# Obs dari JSON lokal (D:\nwp-data\obs) — jangan fetch BMKG API di scheduler
USE_LOCAL_OBS_JSON = os.getenv("USE_LOCAL_OBS_JSON", "false").lower() in ("1", "true", "yes")

MODELS = ["InaNWP", "InaCAWO", "GFS", "IFS"]

# Model tanpa NC → forecast dummy derived dari InaNWP
DUMMY_MODELS = [m.strip() for m in os.getenv("DUMMY_MODELS", "InaCAWO,GFS,IFS").split(",") if m.strip()]
USE_DUMMY_MODELS = os.getenv("USE_DUMMY_MODELS", "true").lower() in ("1", "true", "yes")
REAL_MODELS = [m for m in MODELS if m not in DUMMY_MODELS]

# All Sinoptik parameters from API Export Sinoptik (slide 8)
SINOPTIK_PARAMETERS = [
    "station_name", "data_timestamp",
    "wind_dir_deg_dd", "wind_indicator_iw", "wind_speed_ff", "visibility_vv",
    "weather_indicator_ix", "present_weather_ww", "past_weather_w1", "past_weather_w2",
    "pressure_reading_mb", "pressure_temp_c", "rainfall_indicator_ir", "rainfall_last_mm",
    "temp_drybulb_c_tttttt", "temp_max_c_txtxtx", "temp_min_c_tntntn", "temp_wetbulb_c",
    "cloud_cover_oktas_m", "cloud_low_type_cl", "cloud_med_type_cm", "cloud_high_type_ch",
    "cloud_low_cover_oktas", "cloud_med_cover_oktas", "cloud_high_cover_oktas",
    "cloud_low_type_1", "cloud_low_type_2", "cloud_low_type_3",
    "cloud_med_type_1", "cloud_med_type_2", "cloud_high_type_1", "cloud_high_type_2",
    "cloud_low_base_1", "cloud_low_base_2", "cloud_low_base_3",
    "cloud_med_base_1", "cloud_med_base_2", "cloud_high_base_1", "cloud_high_base_2",
    "cloud_low_peak_1", "cloud_low_peak_2",
    "cloud_low_cover_1", "cloud_low_cover_2", "cloud_low_cover_3",
    "cloud_med_cover_1", "cloud_med_cover_2", "cloud_high_cover_1", "cloud_high_cover_2",
    "cloud_low_dir_1", "cloud_low_dir_2", "cloud_low_dir_3",
    "cloud_med_dir_1", "cloud_med_dir_2", "cloud_high_dir_1", "cloud_high_dir_2",
    "cloud_elevation_angle_ec_1", "cloud_elevation_angle_ec_2",
    "cloud_low_dir_true_da_1", "cloud_low_dir_true_da_2", "cloud_vertical_vis",
    "solar_rad_24h_jcm2_f24", "sunshine_h_sss", "evaporation_24hours_mm_eee",
    "evaporation_eq_indicator_ie", "land_cond", "land_note", "edited_encoded_synop",
    "wind_dir_deg_dd_digi_flag", "wind_speed_ff_digi_flag", "rainfall_last_mm_digi_flag",
    "temp_drybulb_c_tttttt_digi_flag", "temp_max_c_txtxtx_digi_flag",
    "temp_min_c_tntntn_digi_flag", "temp_wetbulb_c_digi_flag",
    "solar_rad_24h_jcm2_f24_digi_flag", "sunshine_h_sss_digi_flag",
    "evaporation_24hours_mm_eee_digi_flag", "pressure_qff_mb_derived_digi_flag",
    "pressure_qfe_mb_derived_digi_flag", "pressure_qff_mb_derived", "pressure_qfe_mb_derived",
    "temp_dewpoint_c_tdtdtd", "relative_humidity_pc",
    "cloud_layer_1_type_c", "cloud_layer_1_height_m_hshs", "cloud_layer_1_amt_oktas_ns",
    "cloud_layer_2_type_c", "cloud_layer_2_height_m_hshs", "cloud_layer_2_amt_oktas_ns",
    "cloud_layer_3_type_c", "cloud_layer_3_height_m_hshs", "cloud_layer_3_amt_oktas_ns",
    "cloud_layer_4_type_c", "cloud_layer_4_height_m_hshs", "cloud_layer_4_amt_oktas_ns",
    "pressure_3h_diff_mb_ppp", "pressure_24h_diff_mb_p24",
    "rainfall_24h_rrrr", "rainfall_6h_rrr", "encoded_synop",
    "created_at", "updated_at", "qc_flag", "qc_histories", "observer_name",
]

# Numeric parameters used for model verification scoring
VERIFY_PARAMETERS = {
    "temp_drybulb_c_tttttt": {"label": "Suhu Udara 2m", "unit": "°C", "category": "continuous"},
    "temp_dewpoint_c_tdtdtd": {"label": "Titik Embun", "unit": "°C", "category": "continuous"},
    "relative_humidity_pc": {"label": "Kelembapan Relatif", "unit": "%", "category": "continuous"},
    "pressure_qff_mb_derived": {"label": "Tekanan QFF", "unit": "hPa", "category": "continuous"},
    "pressure_qfe_mb_derived": {"label": "Tekanan QFE", "unit": "hPa", "category": "continuous"},
    "wind_speed_ff": {"label": "Kecepatan Angin", "unit": "m/s", "category": "continuous"},
    "wind_dir_deg_dd": {"label": "Arah Angin", "unit": "°", "category": "circular"},
    "rainfall_6h_rrr": {"label": "Curah Hujan 6h", "unit": "mm", "category": "continuous"},
    "rainfall_24h_rrrr": {"label": "Curah Hujan 24h", "unit": "mm", "category": "continuous"},
    "cloud_cover_oktas_m": {"label": "Tutupan Awan", "unit": "okta", "category": "continuous"},
    "visibility_vv": {"label": "Jarak Pandang", "unit": "km", "category": "continuous"},
    "temp_max_c_txtxtx": {"label": "Suhu Maksimum", "unit": "°C", "category": "continuous"},
    "temp_min_c_tntntn": {"label": "Suhu Minimum", "unit": "°C", "category": "continuous"},
    "temp_wetbulb_c": {"label": "Suhu Bola Basah", "unit": "°C", "category": "continuous"},
    "pressure_reading_mb": {"label": "Tekanan Bacaan", "unit": "hPa", "category": "continuous"},
    "rainfall_last_mm": {"label": "Curah Hujan Terakhir", "unit": "mm", "category": "continuous"},
}

# Model variable mapping for interpolation
MODEL_VAR_MAP = {
    "InaNWP": {
        "temp_drybulb_c_tttttt": ["T2", "t2m", "T2M"],
        "temp_dewpoint_c_tdtdtd": ["Td2m", "d2m", "TD2", "Td2"],
        "relative_humidity_pc": ["RH2", "rh2m", "RH"],
        "pressure_qff_mb_derived": ["MSLP", "mslp", "PSFC", "sp"],
        "pressure_qfe_mb_derived": ["PSFC", "sp"],
        "pressure_reading_mb": ["PSFC", "sp"],
        "wind_speed_ff": ["WS10", "ws10", "wind_speed"],
        "wind_dir_deg_dd": ["WD10", "wd10"],
        "rainfall_6h_rrr": ["RAINNC", "rainnc", "tp", "precip", "RAINC"],
        "rainfall_24h_rrrr": ["RAINNC", "rainnc", "tp", "precip", "RAINC"],
        "rainfall_last_mm": ["RAINNC", "rainnc", "RAINC"],
        "cloud_cover_oktas_m": ["TCDC", "tcc", "CLDTOT"],
        "temp_max_c_txtxtx": ["T2MAX", "TMAX", "T2"],
        "temp_min_c_tntntn": ["T2MIN", "TMIN", "T2"],
    },
    "InaCAWO": {
        "temp_drybulb_c_tttttt": ["T2", "t2m"],
        "relative_humidity_pc": ["RH2", "rh2m"],
        "pressure_qff_mb_derived": ["MSLP", "mslp", "PSFC"],
        "wind_speed_ff": ["WS10", "ws10"],
        "wind_dir_deg_dd": ["WD10", "wd10"],
        "rainfall_6h_rrr": ["RAINNC", "tp"],
        "cloud_cover_oktas_m": ["TCDC", "tcc"],
    },
    "GFS": {
        "temp_drybulb_c_tttttt": ["t2m", "T2"],
        "relative_humidity_pc": ["rh2m", "RH2"],
        "pressure_qff_mb_derived": ["prmsl", "mslp"],
        "wind_speed_ff": ["ws10"],
        "wind_dir_deg_dd": ["wd10"],
        "rainfall_6h_rrr": ["tp", "apcp"],
        "cloud_cover_oktas_m": ["tcc"],
    },
    "IFS": {
        "temp_drybulb_c_tttttt": ["t2m"],
        "relative_humidity_pc": ["r", "rh2m"],
        "pressure_qff_mb_derived": ["msl"],
        "wind_speed_ff": ["ws10"],
        "wind_dir_deg_dd": ["wd10"],
        "rainfall_6h_rrr": ["tp"],
        "cloud_cover_oktas_m": ["tcc"],
    },
}
