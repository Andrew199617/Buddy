param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$DataDir = '',
    [string]$PythonPath = '',
    [string]$CacheDir = ''
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not $PythonPath) {
    $PythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
if (-not $DataDir) {
    $DataDir = Join-Path $PSScriptRoot 'open-webui-data'
}
$DataDir = [IO.Path]::GetFullPath($DataDir)
if (-not $CacheDir) {
    $CacheDir = Join-Path $DataDir 'cache'
}
$CacheDir = [IO.Path]::GetFullPath($CacheDir)
$frontendPath = Join-Path $PSScriptRoot 'build'
$frontendIndex = Join-Path $frontendPath 'index.html'
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw 'Buddy runtime is missing. Follow the Windows setup in BUDDY.md to create .venv.'
}
if (-not (Test-Path -LiteralPath $frontendIndex)) {
    throw 'Buddy frontend is missing. Run npm ci and npm run build in this folder, then start Buddy again.'
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.python'
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache'
$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache\pip'
# Parallel instances should pass independent data directories. CacheDir can
# point to existing model assets without sharing SQLite databases or static files.
$env:DATA_DIR = $DataDir
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = 'sqlite:///' + ($DataDir.Replace('\', '/') + '/webui.db')
}
$env:STATIC_DIR = Join-Path $env:DATA_DIR 'static'
$env:FRONTEND_BUILD_DIR = $frontendPath
$env:WEBUI_NAME = 'Buddy'
$env:WEBUI_FAVICON_URL = '/static/favicon.png'
$env:HF_HOME = Join-Path $CacheDir 'huggingface'
$env:TORCH_HOME = Join-Path $CacheDir 'torch'
$env:XDG_CACHE_HOME = $CacheDir
$env:NLTK_DATA = Join-Path $CacheDir 'nltk'

# Run Buddy's tracked backend with the pinned dependencies in .venv.
# Frontend assets are copied into DATA_DIR/static without changing .venv.
$backendPath = Join-Path $PSScriptRoot 'backend'
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = $backendPath + [IO.Path]::PathSeparator + $env:PYTHONPATH
} else {
    $env:PYTHONPATH = $backendPath
}
$launcherPath = Join-Path $PSScriptRoot 'local\serve.py'
& $PythonPath $launcherPath serve --host 0.0.0.0 --port $Port
exit $LASTEXITCODE
