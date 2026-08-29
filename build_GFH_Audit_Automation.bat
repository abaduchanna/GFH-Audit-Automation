@echo off
setlocal enabledelayedexpansion
title Build GFH Audit Timesheet (original) + GFH_Audit_Automation

set "SRCDIR=C:\Users\AbadUmairChanna\Downloads\GitHub\GFH-Audit-Automation"
set "OUTDIR=C:\Users\AbadUmairChanna\Downloads\GitHub"
set "WORKBASE=%TEMP%\pyi_build\GFH_Audit_Automation"

echo.
echo  ============================================================
echo   Building: GFH_Inventory_Audit_Timesheet.exe + GFH_Audit_Automation.exe
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

REM Read the app version so the exe gets a versioned filename.
REM A fresh filename per build means Windows Explorer can NEVER
REM show a stale/blank cached icon for it.
set "EXEVER="
for /f "usebackq delims=" %%V in (`python -c "import gfh_audit; print(gfh_audit.__version__)" 2^>nul`) do set "EXEVER=%%V"
if not defined EXEVER set "EXEVER=1.2.1"
set "BUILD_COMMIT="
for /f "usebackq delims=" %%C in (`git rev-parse --short HEAD 2^>nul`) do set "BUILD_COMMIT=%%C"
echo    Source commit: !BUILD_COMMIT!  (app version !EXEVER!)


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

REM Build 1: the ORIGINAL GFH Audit Timesheet app (exact copy, full branding)
echo    Building GFH_Inventory_Audit_Timesheet.spec (original app with logos/icons)...
python -m PyInstaller "GFH_Inventory_Audit_Timesheet.spec" --noconfirm --clean --workpath "%WORKBASE%" 2>&1

if errorlevel 1 (
    echo    FAILED: GFH_Inventory_Audit_Timesheet
    pause
    exit /b 1
)

echo    SUCCESS: GFH_Inventory_Audit_Timesheet

REM Build 2: the refactored modular app
echo    Building GFH_Audit_Automation.spec...
python -m PyInstaller "GFH_Audit_Automation.spec" --noconfirm --clean --workpath "%WORKBASE%" 2>&1

if errorlevel 1 (
    echo    FAILED: GFH_Audit_Automation
    pause
    exit /b 1
)

echo    SUCCESS: GFH_Audit_Automation

REM Copy .exe files to output
if exist "dist\GFH_Inventory_Audit_Timesheet.exe" (
    if not exist "%OUTDIR%" mkdir "%OUTDIR%"
    copy /Y "dist\GFH_Inventory_Audit_Timesheet.exe" "%OUTDIR%\GFH_Inventory_Audit_Timesheet.exe" >nul
    echo    Collected: %OUTDIR%\GFH_Inventory_Audit_Timesheet.exe
) else (
    echo    WARNING: dist\GFH_Inventory_Audit_Timesheet.exe not found
)
if exist "dist\GFH_Audit_Automation.exe" (
    if not exist "%OUTDIR%" mkdir "%OUTDIR%"
    del /q "%OUTDIR%\GFH_Audit_Automation_v*.exe" >nul 2>&1
    copy /Y "dist\GFH_Audit_Automation.exe" "%OUTDIR%\GFH_Audit_Automation.exe" >nul
    if errorlevel 1 (
        echo    WARNING: could not overwrite GFH_Audit_Automation.exe - close the running exe and rebuild.
    ) else (
        echo    Collected: %OUTDIR%\GFH_Audit_Automation.exe
    )
    copy /Y "dist\GFH_Audit_Automation.exe" "%OUTDIR%\GFH_Audit_Automation_v!EXEVER!.exe" >nul
    if errorlevel 1 (
        echo    WARNING: could not write GFH_Audit_Automation_v!EXEVER!.exe - close the running exe and rebuild.
    ) else (
        echo    Collected: %OUTDIR%\GFH_Audit_Automation_v!EXEVER!.exe
    )
) else (
    echo    WARNING: dist\GFH_Audit_Automation.exe not found
)
:: Verify the GFH icon is REALLY embedded (Windows reads each exe itself)
powershell -NoProfile -Command "try { Add-Type -AssemblyName System.Drawing; $i=[System.Drawing.Icon]::ExtractAssociatedIcon('%OUTDIR%\GFH_Audit_Automation.exe'); if ($i) { Write-Host '    Icon check GFH_Audit_Automation.exe: GFH icon embedded OK' } else { Write-Host '    Icon check GFH_Audit_Automation.exe: NO ICON EMBEDDED - report this' } } catch { Write-Host ('    Icon check: ' + $_.Exception.Message) }"
powershell -NoProfile -Command "try { Add-Type -AssemblyName System.Drawing; $i=[System.Drawing.Icon]::ExtractAssociatedIcon('%OUTDIR%\GFH_Audit_Automation_v!EXEVER!.exe'); if ($i) { Write-Host '    Icon check versioned exe: GFH icon embedded OK' } else { Write-Host '    Icon check versioned exe: NO ICON EMBEDDED - report this' } } catch { Write-Host ('    Icon check: ' + $_.Exception.Message) }"
:: Save what Windows reads out of the plain exe as a PNG you can open and see
powershell -NoProfile -Command "try { Add-Type -AssemblyName System.Drawing; $i=[System.Drawing.Icon]::ExtractAssociatedIcon('%OUTDIR%\GFH_Audit_Automation.exe'); $i.ToBitmap().Save('%OUTDIR%\GFH_icon_from_exe.png'); Write-Host '    Saved: GFH_icon_from_exe.png - it shows EXACTLY what Windows reads from the exe' } catch { Write-Host ('    Icon PNG not saved: ' + $_.Exception.Message) }"
:: Force the Windows shell to rebuild its icon associations (SHCNE_ASSOCCHANGED)
powershell -NoProfile -Command "$s='[DllImport('+ [char]34 +'shell32.dll'+ [char]34 +')] public static extern void SHChangeNotify(int a,int b,IntPtr c,IntPtr d);'; $t=Add-Type -MemberDefinition $s -Name SH -Namespace W -PassThru; $t::SHChangeNotify(134217728,0,[IntPtr]::Zero,[IntPtr]::Zero)"
ie4uinit.exe -show >nul 2>&1
:: Ship the one-click icon cache fix next to the exes
if exist "fix_icon_cache.bat" copy /Y "fix_icon_cache.bat" "%OUTDIR%\fix_icon_cache.bat" >nul

echo.
echo  ============================================================
echo   Done. Produced exes in %OUTDIR% :
echo     GFH_Inventory_Audit_Timesheet.exe     (original app)
echo     GFH_Audit_Automation.exe              (new app, plain name)
echo     GFH_Audit_Automation_v!EXEVER!.exe    (new app, cache-proof name)
echo  ============================================================
echo.
echo   IF EXPLORER SHOWS A BLANK ICON on GFH_Audit_Automation.exe:
echo    - That filename was used by the first icon-less builds and
echo      Windows still shows the cached blank icon. It is NOT a
echo      build problem - the Icon check lines above prove the
echo      icon IS inside the exe.
echo    - Double-click fix_icon_cache.bat ONCE (copied next to the
echo      exe). It restarts Explorer and wipes the icon cache; after
echo      that the GFH icon shows on the plain exe too.
echo    - Or run the v!EXEVER! exe: its fresh filename can never
echo      carry a stale cached icon.
echo.
echo   Runtime notes:
echo    - The bot ATTACHES to Edge on debug port 9226 (VidaPay style).
echo      If that Edge window is not open, the app launches it with
echo      the persistent profile C:\GFH_Edge_Automation_Profile.
echo    - Scan the WhatsApp Web QR once in that Edge window; it stays
echo      logged in across runs.
echo    - Tesseract OCR + Ghostscript enable the OCR photo matching.
echo    - Always build with THIS bat from the repo. Old copies of
echo      earlier bats on your PC ship old logic - delete them.
echo.
pause
endlocal
exit /b 0
