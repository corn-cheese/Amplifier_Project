@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=300"

set "TIMESTAMP=%~2"
if "%TIMESTAMP%"=="" (
  for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TIMESTAMP=cin2-series-boost-topology-%%I"
)

set "TOPOLOGY=%~3"
set "TOPOLOGY_ARG="
if not "%TOPOLOGY%"=="" set "TOPOLOGY_ARG=--cin2-boost-topology %TOPOLOGY%"

set "CADENCE_WORKERS=%~4"
if "%CADENCE_WORKERS%"=="" set "CADENCE_WORKERS=1"

pushd "%~dp0\..\..\.."

echo Running CIN2 series-damped boost topology sweep with %TRIALS% trials, timestamp %TIMESTAMP%, Cadence workers %CADENCE_WORKERS%
if not "%TOPOLOGY%"=="" echo Fixed CIN2 boost topology: %TOPOLOGY%
python "%~dp0optuna_q5_bandpass_sweep.py" --repo-root . --config "%~dp0runner_config.json" --family cin2-series-boost-topology --baseline-workspace "Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\best_run\trial_0146\workspace" --trials %TRIALS% --timestamp %TIMESTAMP% --cadence-workers %CADENCE_WORKERS% %TOPOLOGY_ARG%
if errorlevel 1 goto fail

popd
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
