param(
    [int]$Splits = 10,
    [int]$TrialsPerSplit = 50,
    [int]$CadenceWorkers = 1,
    [string]$TimestampPrefix = "",
    [switch]$NoVerify
)

$ErrorActionPreference = "Stop"

if ($Splits -le 0) {
    throw "Splits must be positive."
}
if ($TrialsPerSplit -le 0) {
    throw "TrialsPerSplit must be positive."
}
if ($CadenceWorkers -le 0) {
    throw "CadenceWorkers must be positive."
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($TimestampPrefix)) {
    $TimestampPrefix = "t21-t25-" + (Get-Date -Format "yyyyMMdd-HHmmss")
}

$CandidateSpecPaths = @(
    "tools/topology_specs/t21_t25/T21.json",
    "tools/topology_specs/t21_t25/T22.json",
    "tools/topology_specs/t21_t25/T23.json",
    "tools/topology_specs/t21_t25/T24.json",
    "tools/topology_specs/t21_t25/T25.json"
)

Write-Host "Running T21-T25 targeted workflow"
Write-Host "Starting all candidate split jobs together"
Write-Host "Each candidate: $Splits parallel sweeps x $TrialsPerSplit trials = $($Splits * $TrialsPerSplit) trials per candidate"
Write-Host "Total split jobs: $($CandidateSpecPaths.Count * $Splits)"
Write-Host "Timestamp prefix: $TimestampPrefix"

$SplitLogRoot = Join-Path $RepoRoot "automation_artifacts\sweeps\q5-bandpass-t21-t25-targeted\workflow_logs\split_logs\$TimestampPrefix"
New-Item -ItemType Directory -Force -Path $SplitLogRoot | Out-Null

$baseSeed = 21000
$candidateIndex = 0
$processes = @()
foreach ($specPath in $CandidateSpecPaths) {
    $candidateIndex += 1
    $specId = [System.IO.Path]::GetFileNameWithoutExtension($specPath)
    for ($split = 1; $split -le $Splits; $split++) {
        $timestamp = "{0}-{1}-s{2:D2}" -f $TimestampPrefix, $specId.ToLowerInvariant(), $split
        $seed = $baseSeed + ($candidateIndex * 100) + $split
        $pythonArgs = @(
            "-m", "tools.q5_topology_spec_sweep",
            "--repo-root", ".",
            "--config", "runner_config.json",
            "--topology-spec", $specPath,
            "--trials", "$TrialsPerSplit",
            "--timestamp", $timestamp,
            "--seed", "$seed",
            "--cadence-workers", "$CadenceWorkers"
        )
        if ($NoVerify) {
            $pythonArgs += "--no-verify"
        }

        $stdoutPath = Join-Path $SplitLogRoot "$timestamp.out.log"
        $stderrPath = Join-Path $SplitLogRoot "$timestamp.err.log"
        $process = Start-Process `
            -FilePath "python" `
            -ArgumentList $pythonArgs `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
        $processes += [pscustomobject]@{
            SpecId = $specId
            Split = $split
            Timestamp = $timestamp
            Process = $process
            Stdout = $stdoutPath
            Stderr = $stderrPath
        }
    }
}

Write-Host "Started $($processes.Count) split jobs"
Write-Host "Split logs: $SplitLogRoot"

$failed = @()
foreach ($item in $processes) {
    Wait-Process -Id $item.Process.Id
    $item.Process.Refresh()
    if ($item.Process.ExitCode -ne 0) {
        $failed += $item
    }
}

if ($failed.Count -gt 0) {
    foreach ($item in $failed) {
        Write-Host "Failed $($item.Timestamp), exit code $($item.Process.ExitCode)"
        if (Test-Path -LiteralPath $item.Stderr) {
            Get-Content -LiteralPath $item.Stderr -Tail 40
        }
    }
    throw "Failed sweep jobs: $($failed.Timestamp -join ', ')"
}

$summary = foreach ($item in $processes) {
    [pscustomobject]@{
        spec_id = $item.SpecId
        split = $item.Split
        timestamp = $item.Timestamp
        pid = $item.Process.Id
        exit_code = $item.Process.ExitCode
        stdout = $item.Stdout
        stderr = $item.Stderr
    }
}
$summaryPath = Join-Path $SplitLogRoot "split_processes.json"
$summary | ConvertTo-Json | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "Split process summary: $summaryPath"
Write-Host ""
Write-Host "T21-T25 workflow complete"
