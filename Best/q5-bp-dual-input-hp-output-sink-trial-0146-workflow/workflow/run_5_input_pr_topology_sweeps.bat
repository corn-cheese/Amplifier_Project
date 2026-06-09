@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=2000"

set "RUN_TAG=%~2"
if "%RUN_TAG%"=="" (
  for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_TAG=%%I"
)

set "CADENCE_WORKERS=%~3"
if "%CADENCE_WORKERS%"=="" set "CADENCE_WORKERS=1"

set "PREFIX=q5-trial0146"

echo Launching five trial_0146 input pseudo-resistor sweeps.
echo Trials per topology: %TRIALS%
echo Run tag: %RUN_TAG%
echo Cadence workers per topology: %CADENCE_WORKERS%

call :launch b2b-cc
call :launch b2b-ca
call :launch dual-b2b
call :launch series2-cc
call :launch reverse-antiparallel

echo.
echo Started all five sweeps. Artifact timestamps:
echo   %PREFIX%-b2b-cc-%TRIALS%-%RUN_TAG%
echo   %PREFIX%-b2b-ca-%TRIALS%-%RUN_TAG%
echo   %PREFIX%-dual-b2b-%TRIALS%-%RUN_TAG%
echo   %PREFIX%-series2-cc-%TRIALS%-%RUN_TAG%
echo   %PREFIX%-reverse-antiparallel-%TRIALS%-%RUN_TAG%
exit /b 0

:launch
set "TOPOLOGY=%~1"
set "TIMESTAMP=%PREFIX%-%TOPOLOGY%-%TRIALS%-%RUN_TAG%"
echo Starting %TOPOLOGY% as %TIMESTAMP%
start "sweep %TOPOLOGY%" cmd /c call "%~dp0run_q5_ac_shape_sweeps.bat" %TRIALS% %TIMESTAMP% %TOPOLOGY% %CADENCE_WORKERS%
exit /b 0
