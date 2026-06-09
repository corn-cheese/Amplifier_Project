@echo off
setlocal EnableExtensions

set "TRIALS=%~1"
if "%TRIALS%"=="" set "TRIALS=300"

set "CADENCE_WORKERS=%~2"
if "%CADENCE_WORKERS%"=="" set "CADENCE_WORKERS=20"

set "TIMESTAMP_PREFIX=%~3"
set "NO_VERIFY=%~4"
set "NO_VERIFY_ARG="
if /I "%NO_VERIFY%"=="--no-verify" set "NO_VERIFY_ARG=-NoVerify"
if /I "%NO_VERIFY%"=="no-verify" set "NO_VERIFY_ARG=-NoVerify"

set "SCRIPT_DIR=%~dp0"

pushd "%SCRIPT_DIR%\..\..\.." || exit /b 1

echo Running T21-T25 as five parallel candidate sweeps.
echo Trials per candidate: %TRIALS%
echo Cadence workers per candidate: %CADENCE_WORKERS%
echo Effective maximum concurrent verifier jobs: 5 x %CADENCE_WORKERS%.

if "%TIMESTAMP_PREFIX%"=="" (
  echo Timestamp prefix: auto
  powershell -NoProfile -ExecutionPolicy Bypass -File "run_t21_t25_parallel_sweeps.ps1" -Splits 1 -TrialsPerSplit %TRIALS% -CadenceWorkers %CADENCE_WORKERS% %NO_VERIFY_ARG%
) else (
  echo Timestamp prefix: %TIMESTAMP_PREFIX%
  powershell -NoProfile -ExecutionPolicy Bypass -File "run_t21_t25_parallel_sweeps.ps1" -Splits 1 -TrialsPerSplit %TRIALS% -CadenceWorkers %CADENCE_WORKERS% -TimestampPrefix "%TIMESTAMP_PREFIX%" %NO_VERIFY_ARG%
)
set "EXITCODE=%ERRORLEVEL%"

popd
exit /b %EXITCODE%
