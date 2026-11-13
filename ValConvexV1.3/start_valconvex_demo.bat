@echo off
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 run.py --demo --speed 2
) else (
  python run.py --demo --speed 2
)
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo ValConvex demo failed to start. See logs\startup.log if available.
  echo.
  pause
)
exit /b %RC%
