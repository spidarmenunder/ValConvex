@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
echo Done. Double-click start_valconvex_demo.bat to try it, or start_valconvex.bat with VALORANT open.
pause
