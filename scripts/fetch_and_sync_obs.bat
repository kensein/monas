@echo off
REM Fetch observasi BMKG Juni → sekarang (per bulan) → D:\nwp-data\obs → SFTP litbangweb
cd /d "%~dp0\.."
set PYTHONPATH=.

echo === MONAS: Fetch Juni-sekarang + Sync Observasi ===
python scripts\fetch_obs_local.py --from-june --monthly --sync --import-db
if errorlevel 1 (
  echo GAGAL — cek .env BMKG_USERNAME/PASSWORD dan koneksi intranet
  pause
  exit /b 1
)
echo.
echo Selesai. File JSON per bulan ada di D:\nwp-data\obs dan sudah di-upload ke litbangweb.
echo Lalu di PC: python scripts\test_nc_pipeline.py --full --no-fetch-obs --fresh
pause
