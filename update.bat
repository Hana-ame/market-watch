@echo off
rem Market-watch: manual refresh (double-click). Calls update.sh inside WSL.
wsl.exe -d Ubuntu -e bash /mnt/e/market-watch/update.sh
if errorlevel 1 (
  echo.
  echo [FAIL] refresh failed. Check WSL: wsl.exe -e bash -lc "python3 --version"
) else (
  echo.
  echo [OK] data updated. Reload the browser page to see latest quotes.
)
pause
