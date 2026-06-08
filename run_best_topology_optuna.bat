@echo off
setlocal

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=50"

pushd "%~dp0"
python -m tools.optuna_best_topology_sweep --repo-root . --config runner_config.json --trials %TRIALS%
set "EXITCODE=%ERRORLEVEL%"
popd

exit /b %EXITCODE%
