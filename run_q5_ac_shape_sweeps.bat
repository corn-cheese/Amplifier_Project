@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=2000"

set "TIMESTAMP=%~2"
if "%TIMESTAMP%"=="" set "TIMESTAMP=q5-dual-input-hp-cp3hi-6500-12000-2000"

pushd "%~dp0"

echo Running Q5 dual-input-highpass-output-sink sweep with %TRIALS% trials, timestamp %TIMESTAMP%
python -m tools.optuna_q5_bandpass_sweep --repo-root . --config runner_config.json --family dual-input-highpass-output-sink --trials %TRIALS% --timestamp %TIMESTAMP%
if errorlevel 1 goto fail

popd
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
