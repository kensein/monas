@echo off
REM Fetch observasi BMKG → D:\nwp-data\obs → upload SFTP ke litbangweb
cd /d "%~dp0\.."
set PYTHONPATH=.

echo === MONAS: Fetch + Sync Observasi ===
python scripts\fetch_obs_local.py --days 10 --sync --import-db
if errorlevel 1 (
  echo GAGAL — cek .env BMKG_USERNAME/PASSWORD dan koneksi intranet
  pause
  exit /b 1
)
echo.
echo Selesai. File JSON ada di D:\nwp-data\obs dan sudah di-upload ke litbangweb.
pause
