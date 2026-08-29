@echo off
setlocal
title GFH Audit Automation - Windows Icon Cache Fix

echo.
echo  ============================================================
echo   GFH Telecom LLC - Windows icon cache fix
echo  ============================================================
echo.
echo   Windows caches exe icons by file path. The FIRST builds of
echo   GFH_Audit_Automation.exe had no icon yet, and Windows STILL
echo   shows that old blank icon for this filename even though the
echo   GFH icon is now embedded inside the exe.
echo.
echo   This fix restarts Explorer and wipes the icon cache files.
echo   Nothing else is touched. Your taskbar disappears for 1-2
echo   seconds and comes back. Open windows stay open.
echo.
set /p ANSWER=Run the fix now? [Y/N]: 
if /i not "%ANSWER%"=="Y" (
    echo    Cancelled - nothing was changed.
    pause
    exit /b 0
)

echo.
echo    [1/3] Restarting Explorer...
taskkill /f /im explorer.exe >nul 2>&1

echo    [2/3] Deleting icon cache databases...
del /a /q "%LocalAppData%\IconCache.db" >nul 2>&1
del /a /q "%LocalAppData%\Microsoft\Windows\Explorer\iconcache_*.db" >nul 2>&1

echo    [3/3] Starting Explorer...
start "" explorer.exe

timeout /t 2 /nobreak >nul
echo.
echo   DONE. Now reopen the folder with GFH_Audit_Automation.exe -
echo   the GFH icon should appear within a few seconds.
echo   If a desktop shortcut still shows a blank icon, delete and
echo   recreate that shortcut once (shortcuts cache icons too).
echo.
pause
endlocal
exit /b 0
