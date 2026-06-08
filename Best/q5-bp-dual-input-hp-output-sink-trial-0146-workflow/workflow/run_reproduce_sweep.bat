@echo off
setlocal

pushd "%~dp0\..\..\.."

python "%~dp0optuna_q5_bandpass_sweep.py" ^
  --repo-root . ^
  --config "%~dp0runner_config.json" ^
  --family dual-input-highpass-output-sink ^
  --baseline-workspace "Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\best_run\trial_0146\workspace" ^
  --trials 2000 ^
  --timestamp q5-dual-input-hp-diode-pr-trial0146-2000

set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
