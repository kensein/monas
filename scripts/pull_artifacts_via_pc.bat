@echo off
REM Hub PC: pull f32 store dari litbangweb (SFTP :3346) lalu push ke webpsi.
REM Pola PSIIDN — litbangweb biasanya TIDAK bisa rsync push ke webpsi:22.
REM
REM Usage:
REM   scripts\pull_artifacts_via_pc.bat
REM   scripts\pull_artifacts_via_pc.bat only-pull
REM   scripts\pull_artifacts_via_pc.bat only-push
setlocal EnableExtensions
cd /d "%~dp0\.."

if not defined SFTP_HOST set SFTP_HOST=202.90.199.54
if not defined SFTP_PORT set SFTP_PORT=3346
if not defined SFTP_USER set SFTP_USER=litbangweb
if not defined REMOTE_EXPORT set REMOTE_EXPORT=/opt/lampp/htdocs/wrf/monas_export
if not defined LOCAL_STAGING set LOCAL_STAGING=%TEMP%\monas_artifacts
if not defined WEBPSI_HOST set WEBPSI_HOST=
if not defined WEBPSI_USER set WEBPSI_USER=
if not defined WEBPSI_PATH set WEBPSI_PATH=/var/www/monas/data/artifacts
if not defined WEBPSI_SSH_PORT set WEBPSI_SSH_PORT=22

set MODE=%~1
if "%MODE%"=="" set MODE=all

if "%MODE%"=="only-push" goto PUSH

echo === Pull dari litbangweb %SFTP_USER%@%SFTP_HOST%:%SFTP_PORT%%REMOTE_EXPORT% ===
if exist "%LOCAL_STAGING%" rmdir /s /q "%LOCAL_STAGING%"
mkdir "%LOCAL_STAGING%"
scp -P %SFTP_PORT% -r ^
  %SFTP_USER%@%SFTP_HOST%:%REMOTE_EXPORT%/manifest.json ^
  %SFTP_USER%@%SFTP_HOST%:%REMOTE_EXPORT%/runs ^
  %SFTP_USER%@%SFTP_HOST%:%REMOTE_EXPORT%/obs ^
  "%LOCAL_STAGING%\"
if errorlevel 1 (
  echo Gagal pull. Coba path compute: /opt/lampp/htdocs/monas/compute-data/artifacts
  exit /b 1
)
if not exist "%LOCAL_STAGING%\manifest.json" (
  echo ERROR: manifest.json tidak ada di staging
  exit /b 1
)
echo Pull OK: %LOCAL_STAGING%

if "%MODE%"=="only-pull" (
  echo Hanya pull — staging di %LOCAL_STAGING%
  exit /b 0
)

:PUSH
if "%WEBPSI_HOST%"=="" (
  echo WEBPSI_HOST kosong — set lalu push manual:
  echo   scp -r "%LOCAL_STAGING%\*" USER@WEBPSI:%WEBPSI_PATH%/
  exit /b 0
)
if "%WEBPSI_USER%"=="" (
  echo WEBPSI_USER kosong
  exit /b 1
)
echo === Push ke %WEBPSI_USER%@%WEBPSI_HOST%:%WEBPSI_PATH% ===
ssh -p %WEBPSI_SSH_PORT% %WEBPSI_USER%@%WEBPSI_HOST% "mkdir -p %WEBPSI_PATH%"
scp -P %WEBPSI_SSH_PORT% -r ^
  "%LOCAL_STAGING%\manifest.json" ^
  "%LOCAL_STAGING%\runs" ^
  "%LOCAL_STAGING%\obs" ^
  %WEBPSI_USER%@%WEBPSI_HOST%:%WEBPSI_PATH%/
echo Selesai. Cek di webpsi: curl -s http://127.0.0.1:8013/api/health
exit /b 0
