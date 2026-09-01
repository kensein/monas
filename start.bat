@echo off
REM NWP Verification Dashboard — Windows Local (Opsi B)
cd /d "%~dp0"
set PYTHONPATH=.

REM Load .env (python-dotenv handles this in app, but set for visibility)
if exist .env (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    set "%%a=%%b"
  )
)

echo Installing dependencies...
python -m pip install -q -r requirements.txt

echo.
echo Starting API on port 8013...
start "NWP-API" cmd /k "cd /d %~dp0 && set PYTHONPATH=. && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8013"

timeout /t 3 /nobreak >nul

echo Starting Frontend on port 3013...
start "NWP-Frontend" cmd /k "cd /d %~dp0 && python serve_frontend.py"

echo.
echo ========================================
echo  Dashboard: http://localhost:3013
echo  API:       http://localhost:8013
echo ========================================
echo.
echo Tekan Ctrl+C di jendela ini tidak akan stop server.
echo Tutup jendela "NWP-API" dan "NWP-Frontend" untuk stop.
pause
