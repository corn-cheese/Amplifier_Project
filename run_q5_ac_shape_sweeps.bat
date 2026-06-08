@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=2000"

set "TIMESTAMP=%~2"
if "%TIMESTAMP%"=="" set "TIMESTAMP=q5-q1-cap-feedback-highpass-rerange2-2000"

pushd "%~dp0"

echo Running Q5 q1-cap-feedback-highpass sweep with %TRIALS% trials, timestamp %TIMESTAMP%
python -m tools.optuna_q5_bandpass_sweep --repo-root . --config runner_config.json --family q1-cap-feedback-highpass --trials %TRIALS% --timestamp %TIMESTAMP%
if errorlevel 1 goto fail

popd
exit /b 0

:fail
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
