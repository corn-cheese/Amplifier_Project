$ErrorActionPreference = 'Stop'

$pubPath = Join-Path $env:USERPROFILE '.ssh\eda_langgraph.pub'
if (-not (Test-Path $pubPath)) {
    Write-Host "Public key not found: $pubPath" -ForegroundColor Red
    exit 1
}

$key = (Get-Content $pubPath -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Host "Public key file is empty: $pubPath" -ForegroundColor Red
    exit 1
}

if ($key.Contains("'")) {
    Write-Host "Public key contains an unsupported quote character." -ForegroundColor Red
    exit 1
}

$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$remote = "KEY='$key'; umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF `"`$KEY`" ~/.ssh/authorized_keys || printf '%s\n' `"`$KEY`" >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys"

Write-Host ""
Write-Host "Registering public key for SSH alias: me19" -ForegroundColor Cyan
Write-Host "Only type your server password when the SSH password prompt appears." -ForegroundColor Yellow
Write-Host "Do not type the password at a plain PowerShell prompt." -ForegroundColor Yellow
Write-Host ""

& $ssh me19 $remote
$registerExit = $LASTEXITCODE

if ($registerExit -ne 0) {
    Write-Host ""
    Write-Host "Public key registration failed. Check the SSH error above." -ForegroundColor Red
    exit $registerExit
}

Write-Host ""
Write-Host "Public key registration command completed. Testing key-only login..." -ForegroundColor Cyan
& $ssh -o BatchMode=yes me19 "echo SSH_KEY_OK"
$testExit = $LASTEXITCODE

if ($testExit -eq 0) {
    Write-Host ""
    Write-Host "SSH key login is working." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Registration command ran, but key-only login still failed." -ForegroundColor Red
    exit $testExit
}
