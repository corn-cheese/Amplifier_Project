param(
    [int]$Trials = 300,
    [int]$TimeoutSeconds = 0,
    [int]$CadenceWorkers = 1,
    [string]$TimestampPrefix = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$WorkflowRel = "Best/q5-bp-dual-input-hp-output-sink-trial-0146-workflow/workflow"
$SweepScript = Join-Path $RepoRoot "$WorkflowRel/optuna_q5_bandpass_sweep.py"
$ConfigPath = Join-Path $RepoRoot "$WorkflowRel/runner_config.json"
$BaselineWorkspace = "Best/q5-bp-dual-input-hp-output-sink-trial-0146-workflow/baseline/q5-output-sink-trial-0237-workspace"

if ($TimestampPrefix -eq "") {
    $TimestampPrefix = "area-reduce-" + (Get-Date -Format yyyyMMdd-HHmmss)
}

$Families = @(
    "area-capdown-retune",
    "area-ce2-first-retune"
)

Write-Host "Running parallel amptest_v2p3 area-reduction sweeps from baseline workspace:"
Write-Host "  $BaselineWorkspace"
Write-Host "Trials per family: $Trials"
Write-Host "Cadence workers per family: $CadenceWorkers"

$Jobs = foreach ($Family in $Families) {
    $Timestamp = "$TimestampPrefix-$Family"
    Start-Job -Name $Family -ArgumentList $RepoRoot, $SweepScript, $ConfigPath, $BaselineWorkspace, $Family, $Trials, $TimeoutSeconds, $CadenceWorkers, $Timestamp -ScriptBlock {
        param($RepoRoot, $SweepScript, $ConfigPath, $BaselineWorkspace, $Family, $Trials, $TimeoutSeconds, $CadenceWorkers, $Timestamp)

        $Args = @(
            $SweepScript,
            "--repo-root", ".",
            "--config", $ConfigPath,
            "--family", $Family,
            "--baseline-workspace", $BaselineWorkspace,
            "--trials", [string]$Trials,
            "--timestamp", $Timestamp,
            "--cadence-workers", [string]$CadenceWorkers
        )
        if ($TimeoutSeconds -gt 0) {
            $Args += @("--timeout-seconds", [string]$TimeoutSeconds)
        }

        Push-Location $RepoRoot
        try {
            & python @Args
            $ExitCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }

        if ($ExitCode -ne 0) {
            throw "Sweep $Family failed with exit code $ExitCode"
        }
    }
}

Wait-Job -Job $Jobs | Out-Null

$Failed = @()
foreach ($Job in $Jobs) {
    Write-Host ""
    Write-Host "===== $($Job.Name) ====="
    Receive-Job -Job $Job
    if ($Job.State -ne "Completed") {
        $Failed += $Job.Name
    }
}

Remove-Job -Job $Jobs -Force

if ($Failed.Count -gt 0) {
    throw "Failed sweeps: $($Failed -join ', ')"
}

Write-Host ""
Write-Host "Both area-reduction sweeps completed."
