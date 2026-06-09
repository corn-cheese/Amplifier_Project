@echo off
setlocal EnableExtensions

set "REPO_ROOT=%~dp0..\..\.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%I"

set "WORKFLOW_REL=Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\workflow"
set "SWEEP_SCRIPT=%WORKFLOW_REL%\optuna_q5_bandpass_sweep.py"
set "CONFIG_PATH=%WORKFLOW_REL%\runner_config.json"
set "BASELINE_WORKSPACE=Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\baseline\q5-output-sink-trial-0237-workspace"
set "TIMESTAMP=area-capdown-300-%TS%"
set "CADENCE_WORKERS=%~1"
if "%CADENCE_WORKERS%"=="" set "CADENCE_WORKERS=1"

echo Running 300-trial area-capdown-retune sweep.
echo Baseline: %BASELINE_WORKSPACE%
echo Timestamp: %TIMESTAMP%
echo Cadence workers: %CADENCE_WORKERS%

pushd "%REPO_ROOT%"
python "%SWEEP_SCRIPT%" ^
  --repo-root . ^
  --config "%CONFIG_PATH%" ^
  --family area-capdown-retune ^
  --baseline-workspace "%BASELINE_WORKSPACE%" ^
  --trials 300 ^
  --timestamp "%TIMESTAMP%" ^
  --cadence-workers %CADENCE_WORKERS%
set "EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %EXIT_CODE%
