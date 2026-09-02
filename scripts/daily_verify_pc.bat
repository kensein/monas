@echo off
REM MONAS daily job — jadwalkan Task Scheduler jam 03:00
REM Program: scripts\daily_verify_pc.bat
REM Start in: folder repo monas
cd /d "%~dp0\.."
set PYTHONPATH=.
set PARALLEL_VERIFY=true
set PARALLEL_BACKEND=thread
set ENABLE_PIPELINE_SCHEDULER=false

if not exist logs mkdir logs
echo [%date% %time%] daily_verify_pc start >> logs\daily_verify_bat.log

python scripts\daily_verify_pc.py %*
set ERR=%ERRORLEVEL%
echo [%date% %time%] exit=%ERR% >> logs\daily_verify_bat.log
exit /b %ERR%
