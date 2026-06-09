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

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..\..")
$WorkflowScript = Join-Path $RepoRoot "run_t26_t30_sequential_sweeps.ps1"

Write-Host "Running T26-T30 sequential workflow through PowerShell."
Write-Host "Trials per candidate: $TrialsPerCandidate"
Write-Host "Cadence workers per candidate: $CadenceWorkers"

if ([string]::IsNullOrWhiteSpace($TimestampPrefix)) {
    Write-Host "Timestamp prefix: auto"
    if ($NoVerify) {
        & $WorkflowScript -TrialsPerCandidate $TrialsPerCandidate -CadenceWorkers $CadenceWorkers -NoVerify
    } else {
        & $WorkflowScript -TrialsPerCandidate $TrialsPerCandidate -CadenceWorkers $CadenceWorkers
    }
} else {
    Write-Host "Timestamp prefix: $TimestampPrefix"
    if ($NoVerify) {
        & $WorkflowScript -TrialsPerCandidate $TrialsPerCandidate -CadenceWorkers $CadenceWorkers -TimestampPrefix $TimestampPrefix -NoVerify
    } else {
        & $WorkflowScript -TrialsPerCandidate $TrialsPerCandidate -CadenceWorkers $CadenceWorkers -TimestampPrefix $TimestampPrefix
    }
}
