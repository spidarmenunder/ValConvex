@echo off
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 run.py > logs\startup.log 2>&1
) else (
  python run.py > logs\startup.log 2>&1
)
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo ValConvex failed to start. See logs\startup.log
  echo.
  type logs\startup.log
  echo.
  pause
)
exit /b %RC%
