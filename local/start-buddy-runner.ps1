# Start the Buddy Runner so Buddy (for example in Docker) can run Claude Code
# and Codex on this computer. It listens on 127.0.0.1 only; Docker Desktop
# containers use verified HTTPS at host.docker.internal:<Port>. Supply the TLS
# certificate and private key from the host operator; only its public CA goes
# into Docker. Local loopback HTTP remains available. The Runner key is created
# in %USERPROFILE%\.buddy-runner\key on first start; paste it into Buddy under
# Admin Settings → Connections → Subscriptions → Machines.
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$PythonPath = '',
    [string]$StateDir = '',
    [string]$TlsCertificate = '',
    [string]$TlsKeyFile = '',
    [string[]]$Root = @(),
    [string[]]$Origin = @(),
    [string]$Name = '',
    [switch]$WriteFiles,
    [switch]$AllowHostExecution,
    [switch]$AllowNoOrigin
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

if ([bool]$TlsCertificate -ne [bool]$TlsKeyFile) {
    throw 'Pass -TlsCertificate and -TlsKeyFile together for HTTPS.'
}
if ($TlsCertificate -and -not (Test-Path -LiteralPath $TlsCertificate -PathType Leaf)) {
    throw "The TLS certificate was not found at $TlsCertificate. Supply the operator's PEM certificate chain."
}
if ($TlsKeyFile -and -not (Test-Path -LiteralPath $TlsKeyFile -PathType Leaf)) {
    throw "The TLS private key was not found at $TlsKeyFile. Keep it on the host and supply its path."
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python was not found at $PythonPath. Pass -PythonPath to an environment with Buddy's dependencies."
}

$backendPath = Join-Path $repoRoot 'backend'
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = $backendPath + [IO.Path]::PathSeparator + $env:PYTHONPATH
} else {
    $env:PYTHONPATH = $backendPath
}
$env:PYTHONIOENCODING = 'utf-8'

$runnerArgs = @('-m', 'open_webui.utils.subscriptions.runner', '--port', $Port)
if ($StateDir) {
    $runnerArgs += @('--state-dir', $StateDir)
}
if ($TlsCertificate) {
    $runnerArgs += @('--tls-cert', $TlsCertificate, '--tls-key', $TlsKeyFile)
}
foreach ($directory in $Root) {
    $runnerArgs += @('--root', $directory)
}
foreach ($browserOrigin in $Origin) {
    $runnerArgs += @('--origin', $browserOrigin)
}
if ($Name) { $runnerArgs += @('--name', $Name) }
if ($WriteFiles) { $runnerArgs += '--write' }
if ($AllowHostExecution) { $runnerArgs += '--allow-host-execution' }
if ($AllowNoOrigin) { $runnerArgs += '--allow-no-origin' }
& $PythonPath @runnerArgs
exit $LASTEXITCODE
