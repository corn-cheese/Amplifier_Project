@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=2000"

set "TIMESTAMP=%~2"
if "%TIMESTAMP%"=="" set "TIMESTAMP=q5-dual-input-hp-diode-pr-trial0146-2000"

set "TOPOLOGY=%~3"
set "TOPOLOGY_ARG="
if not "%TOPOLOGY%"=="" set "TOPOLOGY_ARG=--input-pr-topology %TOPOLOGY%"

pushd "%~dp0\..\..\.."

echo Running Q5 trial_0146 dual-input diode pseudo-resistor sweep with %TRIALS% trials, timestamp %TIMESTAMP%
python "%~dp0optuna_q5_bandpass_sweep.py" --repo-root . --config "%~dp0runner_config.json" --family dual-input-highpass-output-sink --baseline-workspace "Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\best_run\trial_0146\workspace" --trials %TRIALS% --timestamp %TIMESTAMP% %TOPOLOGY_ARG%
if errorlevel 1 goto fail

popd
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
