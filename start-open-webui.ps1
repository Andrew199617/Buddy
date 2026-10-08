$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.python'
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache'
$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache\pip'
$env:DATA_DIR = Join-Path $PSScriptRoot 'open-webui-data'
$env:HF_HOME = Join-Path $env:DATA_DIR 'cache\huggingface'
$env:TORCH_HOME = Join-Path $env:DATA_DIR 'cache\torch'
$env:XDG_CACHE_HOME = Join-Path $env:DATA_DIR 'cache'
$env:NLTK_DATA = Join-Path $env:DATA_DIR 'cache\nltk'
# local\serve.py = open-webui.exe plus the runtime patches in local\owui_local_patches.py.
$launcher = Join-Path $PSScriptRoot 'local\serve.py'
if (Test-Path -LiteralPath $launcher) {
    & "$PSScriptRoot\.venv\Scripts\python.exe" $launcher serve --host 0.0.0.0 --port 8080
} else {
    & "$PSScriptRoot\.venv\Scripts\open-webui.exe" serve --host 0.0.0.0 --port 8080
}
exit $LASTEXITCODE
