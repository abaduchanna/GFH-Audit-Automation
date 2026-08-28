@echo off
setlocal enabledelayedexpansion
title Build GFH_Audit_Automation

set "SRCDIR=C:\Users\AbadUmairChanna\Downloads\GitHub\GFH-Audit-Automation"
set "OUTDIR=C:\Users\AbadUmairChanna\Downloads\GitHub"
set "WORKBASE=%TEMP%\pyi_build\GFH_Audit_Automation"

echo.
echo  ============================================================
echo   Building: GFH_Audit_Automation.exe
echo  ============================================================
echo.

REM Check prerequisites
python --version >nul 2>&1
if errorlevel 1 (
    echo    ERROR: Python not found in PATH.
    pause
    exit /b 1
)
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo    PyInstaller not found. Installing...
    python -m pip install --upgrade pyinstaller
)
git --version >nul 2>&1
if errorlevel 1 (
    echo    ERROR: Git not found in PATH.
    pause
    exit /b 1
)
echo    Prerequisites OK
echo.

REM Clone or pull
if exist "%SRCDIR%" (
    echo    Pulling latest...
    cd "%SRCDIR%"
    git pull 2>&1
) else (
    echo    Cloning GFH-Audit-Automation...
    git clone "https://github.com/abaduchanna/GFH-Audit-Automation.git" "%SRCDIR%" 2>&1
)

cd "%SRCDIR%"

REM Clean previous build
echo    Cleaning previous build...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "__pycache__" rmdir /s /q "__pycache__"
del /s /q *.pyc 2>nul

REM Redirect workpath to TEMP
if exist "%WORKBASE%" rmdir /s /q "%WORKBASE%"
mkdir "%WORKBASE%" 2>nul

REM Install deps
if exist "requirements.txt" (
    echo    Installing requirements...
    python -m pip install -r requirements.txt --quiet 2>nul
)

REM Build
echo    Building GFH_Audit_Automation.spec...
python -m PyInstaller "GFH_Audit_Automation.spec" --noconfirm --clean --workpath "%WORKBASE%" 2>&1

if errorlevel 1 (
    echo    FAILED: GFH_Audit_Automation
    pause
    exit /b 1
)

echo    SUCCESS: GFH_Audit_Automation

REM Copy .exe to output
if exist "dist\GFH_Audit_Automation.exe" (
    if not exist "%OUTDIR%" mkdir "%OUTDIR%"
    copy /Y "dist\GFH_Audit_Automation.exe" "%OUTDIR%\GFH_Audit_Automation.exe" >nul
    echo    Collected: %OUTDIR%\GFH_Audit_Automation.exe
) else (
    echo    WARNING: dist\GFH_Audit_Automation.exe not found
)

echo.
echo  ============================================================
echo   Done: GFH_Audit_Automation.exe
echo  ============================================================
echo.
echo   Runtime notes:
echo    - The bot ATTACHES to Edge on debug port 9226 (VidaPay style).
echo      If that Edge window is not open, the app launches it with
echo      the persistent profile C:\GFH_Edge_Automation_Profile.
echo    - Scan the WhatsApp Web QR once in that Edge window; it stays
echo      logged in across runs.
echo    - Tesseract OCR + Ghostscript enable the OCR photo matching.
echo.
pause
endlocal
exit /b 0
