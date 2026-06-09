$ErrorActionPreference = 'Stop'

$pubPath = Join-Path $env:USERPROFILE '.ssh\eda_langgraph.pub'
if (-not (Test-Path $pubPath)) {
    Write-Host "Public key not found: $pubPath" -ForegroundColor Red
    exit 1
}

$key = (Get-Content $pubPath -Raw).Trim()
if ($key.Contains("'")) {
    Write-Host "Public key contains an unsupported quote character." -ForegroundColor Red
    exit 1
}

$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$remote = @"
KEY='$key'
echo '--- account ---'
whoami
printf 'HOME=%s\n' "`$HOME"
id
echo '--- paths ---'
pwd
ls -ld "`$HOME" "`$HOME/.ssh" "`$HOME/.ssh/authorized_keys" 2>&1
echo '--- authorized_keys exact match ---'
grep -n -x -F "`$KEY" "`$HOME/.ssh/authorized_keys" || echo 'NO_EXACT_MATCH'
echo '--- authorized_keys tail ---'
tail -n 5 "`$HOME/.ssh/authorized_keys" 2>&1
echo '--- sshd hints ---'
test -w "`$HOME/.ssh/authorized_keys" && echo 'authorized_keys writable by user'
"@

Write-Host ""
Write-Host "Running remote diagnostics for me19 key login." -ForegroundColor Cyan
Write-Host "Type the server password only when the SSH password prompt appears." -ForegroundColor Yellow
Write-Host ""

& $ssh me19 $remote
exit $LASTEXITCODE
