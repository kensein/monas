@echo off
REM PC BMKG — fetch observasi harian + SFTP ke litbangweb /opt/lampp/htdocs/wrf/monas_obs
REM Task Scheduler: tiap hari jam 02:00 (sebelum compute litbangweb ~04:30)
REM Start in: folder repo monas
cd /d "%~dp0\.."
set PYTHONPATH=.

if not exist logs mkdir logs
echo [%date% %time%] daily_obs_pc start >> logs\daily_obs_pc.log

REM --days 3: cukup untuk init NC terbaru; --sync: upload SFTP ke litbangweb
REM Tidak --from-june agar cepat (full history sudah pernah di-fetch)
python scripts\fetch_obs_local.py --days 3 --monthly --sync
set ERR=%ERRORLEVEL%

echo [%date% %time%] exit=%ERR% >> logs\daily_obs_pc.log
if %ERR% NEQ 0 (
  echo GAGAL fetch/sync obs — cek BMKG + SFTP di .env
  exit /b %ERR%
)
echo OK — obs di D:\nwp-data\obs dan sudah di-SFTP ke litbangweb monas_obs
exit /b 0
