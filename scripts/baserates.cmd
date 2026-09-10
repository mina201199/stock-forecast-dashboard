@echo off
rem ---------------------------------------------------------------------------
rem Weekly task: recompute signal base rates.  Registered as
rem StockForecast-BaseRates.
rem
rem Kept separate from the daily task on purpose.  Base rates walk the whole
rem price history and are the thing that decides whether any signal may be
rem called a buy point (spec section 3.6) -- they should change because more
rem history accumulated, not silently every morning alongside the fetch.
rem
rem Weekly rather than manual because the history does grow: a base rate
rem measured months ago describes a shorter sample than the one on disk.
rem
rem ASCII-only for the same codepage reason as daily.cmd.
rem ---------------------------------------------------------------------------
setlocal

set "PROJ=%~dp0.."
set "PY=C:\Users\User\anaconda3\python.exe"
set "PYTHONIOENCODING=utf-8"
set "LOGDIR=%PROJ%\logs"
set "LOG=%LOGDIR%\baserates.log"

if not exist "%PY%" (
    echo Python not found at "%PY%" 1>&2
    exit /b 9
)
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%PROJ%"
if errorlevel 1 exit /b 1

echo.>> "%LOG%"
echo ==== START %DATE% %TIME% ====>> "%LOG%"

"%PY%" run.py --baserates>> "%LOG%" 2>&1
if errorlevel 1 (
    echo ==== FAILED: baserates exit=%ERRORLEVEL% ====>> "%LOG%"
    exit /b %ERRORLEVEL%
)

echo ==== OK %DATE% %TIME% ====>> "%LOG%"
exit /b 0
