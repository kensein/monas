@echo off
REM Build + export Docker image monas-compute (jalankan di PC online)
cd /d "%~dp0\.."
if not exist dist mkdir dist
set IMAGE=monas-compute:latest
set OUT=dist\monas-compute.tar.gz

echo Building %IMAGE% ...
docker build -f docker/compute/Dockerfile -t %IMAGE% .
if errorlevel 1 exit /b 1

echo Saving %OUT% ...
docker save %IMAGE% -o dist\monas-compute.tar
if errorlevel 1 exit /b 1

REM gzip via PowerShell jika ada; else tar mentah
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
