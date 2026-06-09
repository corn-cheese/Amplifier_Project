param(
    [int]$TrialsPerCandidate = 300,
    [int]$CadenceWorkers = 3,
    [string]$TimestampPrefix = "",
    [switch]$NoVerify
)

$ErrorActionPreference = "Stop"

if ($TrialsPerCandidate -le 0) {
    throw "TrialsPerCandidate must be positive."
}
if ($CadenceWorkers -le 0) {
    throw "CadenceWorkers must be positive."
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($TimestampPrefix)) {
    $TimestampPrefix = "t26-t30-" + (Get-Date -Format "yyyyMMdd-HHmmss")
}

$CandidateSpecPaths = @(
    "tools/topology_specs/t26_t30/T26.json",
    "tools/topology_specs/t26_t30/T30.json",
    "tools/topology_specs/t26_t30/T29.json",
    "tools/topology_specs/t26_t30/T27.json",
    "tools/topology_specs/t26_t30/T28.json"
)

Write-Host "Running T26-T30 targeted workflow"
Write-Host "Priority order: T26 -> T30 -> T29 -> T27 -> T28"
Write-Host "Candidates run sequentially; trials inside each candidate use Cadence workers."
Write-Host "Trials per candidate: $TrialsPerCandidate"
Write-Host "Cadence workers per candidate: $CadenceWorkers"
Write-Host "Timestamp prefix: $TimestampPrefix"

$LogRoot = Join-Path $RepoRoot "automation_artifacts\sweeps\q5-bandpass-t26-t30-targeted\workflow_logs\sequential_logs\$TimestampPrefix"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

$baseSeed = 26000
$index = 0
$summary = @()
foreach ($specPath in $CandidateSpecPaths) {
    $index += 1
    $specId = [System.IO.Path]::GetFileNameWithoutExtension($specPath)
    $timestamp = "{0}-{1}" -f $TimestampPrefix, $specId.ToLowerInvariant()
    $seed = $baseSeed + ($index * 100)
    $stdoutPath = Join-Path $LogRoot "$timestamp.out.log"
    $stderrPath = Join-Path $LogRoot "$timestamp.err.log"
    $pythonArgs = @(
        "-m", "tools.q5_topology_spec_sweep",
        "--repo-root", ".",
        "--config", "runner_config.json",
        "--topology-spec", $specPath,
        "--trials", "$TrialsPerCandidate",
        "--timestamp", $timestamp,
        "--seed", "$seed",
        "--cadence-workers", "$CadenceWorkers"
    )
    if ($NoVerify) {
        $pythonArgs += "--no-verify"
    }

    Write-Host ""
    Write-Host "Starting $specId as $timestamp"
    & python @pythonArgs > $stdoutPath 2> $stderrPath
    $exitCode = $LASTEXITCODE
    $summary += [pscustomobject]@{
        spec_id = $specId
        timestamp = $timestamp
        seed = $seed
        trials = $TrialsPerCandidate
        cadence_workers = $CadenceWorkers
        exit_code = $exitCode
        stdout = $stdoutPath
        stderr = $stderrPath
    }
    if ($exitCode -ne 0) {
        Write-Host "Failed $specId, exit code $exitCode"
        if (Test-Path -LiteralPath $stderrPath) {
            Get-Content -LiteralPath $stderrPath -Tail 60
        }
        throw "Failed sweep candidate: $specId"
    }
    Write-Host "Finished $specId"
}

$summaryPath = Join-Path $LogRoot "sequential_processes.json"
$summary | ConvertTo-Json | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host ""
Write-Host "Sequential process summary: $summaryPath"
Write-Host "T26-T30 workflow complete"
