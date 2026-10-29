@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo        ValConvex single-file builder
echo ========================================
echo.

echo Cleaning previous PyInstaller output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

where py >nul 2>&1
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo.
    echo Failed to install/build dependencies.
    echo Make sure Python 3.10+ and internet access are available.
    pause
    exit /b 1
)

if not exist valconvex.ico (
    echo.
    echo ERROR: valconvex.ico is missing.
    pause
    exit /b 1
)

echo.
echo Building ValConvex.exe with the dedicated ValConvex icon...
%PY% -m PyInstaller --clean --noconfirm ValConvex.spec
if errorlevel 1 (
    echo.
    echo Build failed. Check the messages above.
    pause
    exit /b 1
)

if not exist dist\ValConvex.exe (
    echo.
    echo ERROR: dist\ValConvex.exe was not created.
    pause
    exit /b 1
)

echo.
echo SUCCESS:
echo dist\ValConvex.exe
echo.
echo NOTE: If Windows still shows an old icon on an existing shortcut,
echo delete that shortcut and create a new shortcut to dist\ValConvex.exe.
echo Windows caches shortcut icons independently of the executable.
echo.
echo The app itself also uses valconvex.ico as its Windows window icon.
pause
