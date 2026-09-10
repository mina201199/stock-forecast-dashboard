@echo off
rem ---------------------------------------------------------------------------
rem Daily task: fetch -> analyse -> digest.  Registered as StockForecast-Daily.
rem
rem This file is deliberately ASCII-only.  cmd.exe decodes .cmd files with the
rem OEM codepage (cp950 here), so any CJK literal in this file -- including the
rem project path, which contains Chinese -- would be mangled.  %~dp0 resolves
rem the project root at runtime instead, so no literal path is needed.
rem
rem Python output is forced to UTF-8, and this file emits only ASCII, so the
rem log file stays valid UTF-8 throughout.
rem
rem Scheduled Task Manager discards stdout, so everything is appended to
rem logs\daily.log.  That log plus meta.last_success (shown in the digest
rem header, which warns after 3 days) is the failure visibility described in
rem spec section 7.3 -- a silently dead task is the main hazard here, because
rem news and analyst targets cannot be backfilled.
rem ---------------------------------------------------------------------------
setlocal

set "PROJ=%~dp0.."
set "PY=C:\Users\User\anaconda3\python.exe"
set "PYTHONIOENCODING=utf-8"
set "LOGDIR=%PROJ%\logs"
set "LOG=%LOGDIR%\daily.log"

if not exist "%PY%" (
    echo Python not found at "%PY%" 1>&2
    exit /b 9
)
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%PROJ%"
if errorlevel 1 exit /b 1

echo.>> "%LOG%"
echo ==== START %DATE% %TIME% ====>> "%LOG%"

"%PY%" run.py>> "%LOG%" 2>&1
if errorlevel 1 (
    echo ==== FAILED: fetch/analyse exit=%ERRORLEVEL% ====>> "%LOG%"
    exit /b %ERRORLEVEL%
)

"%PY%" run.py --digest>> "%LOG%" 2>&1
if errorlevel 1 (
    echo ==== FAILED: digest exit=%ERRORLEVEL% ====>> "%LOG%"
    exit /b %ERRORLEVEL%
)

echo ==== OK %DATE% %TIME% ====>> "%LOG%"
exit /b 0
