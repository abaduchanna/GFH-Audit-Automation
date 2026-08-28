@echo off
setlocal EnableExtensions EnableDelayedExpansion
title GFH Audit Automation - Pull and Build EXE

:: ============================================================
::  GFH Audit Automation - pull the repo and build the EXE.
::
::  Safe to run any time. Every run:
::    1. Checks Git + Python 3.10+ are available
::    2. Clones the repo (first run) or pulls the latest code
::    3. Creates/updates a .venv and installs dependencies
::    4. Warns about optional runtime tools (OCR / Chrome)
::    5. Rebuilds dist\GFHAuditAutomation.exe from scratch
::    6. Drops a dated copy into releases\
::
::  Optional configuration (set before calling this script):
::     set GFH_REPO_URL=https://github.com/you/your-fork.git
::     set GFH_WORKDIR=D:\GFH-Audit-Automation
:: ============================================================

if "%GFH_REPO_URL%"=="" (set "REPO_URL=https://github.com/abaduchanna/GFH-Audit-Automation.git") else (set "REPO_URL=%GFH_REPO_URL%")
if "%GFH_WORKDIR%"=="" (set "WORKDIR=%USERPROFILE%\GFH-Audit-Automation") else (set "WORKDIR=%GFH_WORKDIR%")

echo ============================================================
echo   GFH Audit Automation - Pull and Build
echo   Repo    : %REPO_URL%
echo   Workdir : %WORKDIR%
echo ============================================================
echo.

:: ---------- [1/6] prerequisites ----------
echo [1/6] Checking prerequisites...

where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] "git" was not found on PATH.
    echo         Install Git for Windows: https://git-scm.com/download/win
    set "FAIL_RC=1"
    goto :pause_end
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] "python" was not found on PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/windows/
    echo         IMPORTANT: tick "Add python.exe to PATH" in the installer.
    set "FAIL_RC=1"
    goto :pause_end
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required. Current version:
    python --version
    set "FAIL_RC=1"
    goto :pause_end
)
echo       git + python OK
echo.

:: ---------- [2/6] clone or pull ----------
echo [2/6] Getting latest source code...

if exist "%WORKDIR%\.git" (
    cd /d "%WORKDIR%"
    git stash --include-untracked --quiet >nul 2>nul
    git pull --ff-only origin main
    if errorlevel 1 (
        echo [WARN ] fast-forward pull failed - hard-resetting to origin/main...
        git fetch origin
        if errorlevel 1 (
            echo [ERROR] git fetch failed - check your internet connection.
            set "FAIL_RC=1"
            goto :pause_end
        )
        git reset --hard origin/main
        if errorlevel 1 (
            set "FAIL_RC=1"
            goto :pause_end
        )
    )
    git stash pop --quiet >nul 2>nul
) else (
    if exist "%WORKDIR%\main.py" (
        echo [WARN ] "!WORKDIR!" already has sources but no .git folder.
        echo         Building the local copy as-is; it cannot auto-update.
        cd /d "!WORKDIR!"
    ) else (
        echo       First run - cloning the repository...
        git clone --branch main "%REPO_URL%" "%WORKDIR%"
        if errorlevel 1 (
            set "FAIL_RC=1"
            goto :pause_end
        )
        cd /d "%WORKDIR%"
    )
)
echo       Source code is up to date.
echo.

:: ---------- [3/6] python environment ----------
echo [3/6] Preparing Python environment...

if not exist ".venv\Scripts\python.exe" (
    echo       Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        set "FAIL_RC=1"
        goto :pause_end
    )
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    set "FAIL_RC=1"
    goto :pause_end
)

python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    set "FAIL_RC=1"
    goto :pause_end
)
echo       Installing dependencies from requirements.txt...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies from requirements.txt.
    set "FAIL_RC=1"
    goto :pause_end
)
echo       Installing PyInstaller...
pip install --upgrade pyinstaller
if errorlevel 1 (
    set "FAIL_RC=1"
    goto :pause_end
)
echo.

:: ---------- [4/6] optional runtime tools ----------
echo [4/6] Checking optional runtime tools (warnings only)...

set "TESS_OK=0"
where tesseract >nul 2>nul && set "TESS_OK=1"
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS_OK=1"
if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" set "TESS_OK=1"
if "!TESS_OK!"=="0" (
    echo [WARN ] Tesseract OCR not found. OCR photo matching stays disabled until
    echo         installed: https://github.com/UB-Mannheim/tesseract/wiki
)

set "GS_OK=0"
where gswin64c >nul 2>nul && set "GS_OK=1"
if exist "%ProgramFiles%\gs" set "GS_OK=1"
if "!GS_OK!"=="0" (
    echo [WARN ] Ghostscript not found. PDF rasterising for OCR stays disabled until
    echo         installed: https://www.ghostscript.com/download/gsdnld.html
)

set "CHROME_OK=0"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_OK=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_OK=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_OK=1"
if "!CHROME_OK!"=="0" (
    echo [WARN ] Google Chrome not detected - WhatsApp Web / portal automation needs it.
)
echo.

:: ---------- [5/6] build ----------
echo [5/6] Building EXE with PyInstaller (this can take a few minutes)...

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

".venv\Scripts\pyinstaller.exe" --noconfirm --clean "gfh_audit.spec"
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed - read the messages above.
    set "FAIL_RC=1"
    goto :pause_end
)

if not exist "dist\GFHAuditAutomation.exe" (
    echo [ERROR] PyInstaller finished but dist\GFHAuditAutomation.exe is missing.
    set "FAIL_RC=1"
    goto :pause_end
)
echo.

:: ---------- [6/6] collect output ----------
echo [6/6] Collecting output...

set "TS="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "TS=%%i"

if not "!TS!"=="" (
    if not exist "releases" mkdir "releases"
    copy /y "dist\GFHAuditAutomation.exe" "releases\GFHAuditAutomation_!TS!.exe" >nul
)

echo.
echo ============================================================
echo   BUILD SUCCESSFUL
echo.
echo   EXE        : %WORKDIR%\dist\GFHAuditAutomation.exe
if not "!TS!"=="" echo   Dated copy : %WORKDIR%\releases\GFHAuditAutomation_!TS!.exe
echo.
echo   Notes for the PC that RUNS the exe:
echo     - Google Chrome must be installed (WhatsApp Web automation)
echo     - Tesseract OCR + Ghostscript enable the OCR photo matching
echo     - First launch creates its data files next to the exe
echo ============================================================
set "FAIL_RC=0"
goto :pause_end

:pause_end
pause
endlocal & exit /b %FAIL_RC%
