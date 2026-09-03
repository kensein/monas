@echo off
REM Build + export Docker image monas-compute (jalankan di PC online)
cd /d "%~dp0\.."
if not exist dist mkdir dist
set IMAGE=monas-compute:latest
set OUT=dist\monas-compute.tar.gz

where docker >nul 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: perintah "docker" tidak ditemukan.
  echo.
  echo Opsi:
  echo   1^) Install Docker Desktop untuk Windows:
  echo      https://docs.docker.com/desktop/setup/install/windows-install/
  echo      Setelah install: buka Docker Desktop, tunggu "Engine running",
  echo      tutup+buka ulang PowerShell, lalu jalankan lagi script ini.
  echo.
  echo   2^) Build di mesin lain yang sudah ada Docker + internet
  echo      ^(mis. webpsi / laptop lain^), lalu:
  echo      docker build -f docker/compute/Dockerfile -t monas-compute:latest .
  echo      docker save monas-compute:latest -o monas-compute.tar
  echo      scp -P 3346 monas-compute.tar litbangweb@HOST:/home/litbangweb/
  echo.
  echo   3^) Lihat docs\DEPLOY_LITBANGWEB_WEBPSI.md bagian "Build image"
  echo.
  exit /b 1
)

echo Building %IMAGE% ...
docker build -f docker/compute/Dockerfile -t %IMAGE% .
if errorlevel 1 exit /b 1

echo Saving %OUT% ...
docker save %IMAGE% -o dist\monas-compute.tar
if errorlevel 1 exit /b 1

where gzip >nul 2>&1
if %ERRORLEVEL%==0 (
  gzip -f dist\monas-compute.tar
  echo Saved dist\monas-compute.tar.gz
) else (
  echo Saved dist\monas-compute.tar — compress manual or copy as-is
  echo   scp -P 3346 dist\monas-compute.tar litbangweb@202.90.199.54:/home/litbangweb/
)

echo.
echo Di litbangweb:
echo   gunzip -c monas-compute.tar.gz ^| docker load
echo   # atau: docker load -i monas-compute.tar
