@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=300"

set "TIMESTAMP=%~2"
if "%TIMESTAMP%"=="" set "TIMESTAMP=area-capdown-tight-300"

set "BASELINE_WORKSPACE=%~3"
if "%BASELINE_WORKSPACE%"=="" set "BASELINE_WORKSPACE=D:\Codex\Amplifier\Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\best_run\trial_0146\workspace"

set "CADENCE_WORKERS=%~4"
if "%CADENCE_WORKERS%"=="" set "CADENCE_WORKERS=1"

pushd "%~dp0"

echo Running Q5 tight area-capdown retune sweep with %TRIALS% trials, timestamp %TIMESTAMP%, Cadence workers %CADENCE_WORKERS%
python -m tools.optuna_q5_bandpass_sweep --repo-root . --config runner_config.json --family area-capdown-tight-retune --baseline-workspace "%BASELINE_WORKSPACE%" --trials %TRIALS% --timestamp %TIMESTAMP% --cadence-workers %CADENCE_WORKERS%
if errorlevel 1 goto fail

popd
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
