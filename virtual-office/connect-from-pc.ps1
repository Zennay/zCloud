# Run in PowerShell on your OWN Windows computer, not on the VPS.
# Example: .\\connect-from-pc.ps1 -SshTarget 'ubuntu@198.244.191.182'
param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$SshTarget
)
$ErrorActionPreference = 'Stop'
if ($SshTarget.StartsWith('-')) { throw 'Ongeldige SSH-doelhost.' }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
  throw 'OpenSSH Client is niet geïnstalleerd op deze pc.'
}
Write-Host 'Office via eigen pc: http://127.0.0.1:3422/'
Write-Host 'Tampermonkey in een gewone ChatGPT-tab: http://127.0.0.1:3422'
Write-Host 'Laat dit venster open. Stop met Ctrl+C.'
& ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=25 -o ServerAliveCountMax=3 `
  -L '127.0.0.1:3422:127.0.0.1:3422' -- $SshTarget
if ($LASTEXITCODE -ne 0) { throw "SSH-tunnel afgebroken (exit $LASTEXITCODE)." }
