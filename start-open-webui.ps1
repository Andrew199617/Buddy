param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$DataDir = '',
    [string]$PythonPath = '',
    [string]$CacheDir = ''
)

# Compatibility entry point for existing shortcuts. Forward named parameters.
& (Join-Path $PSScriptRoot 'start-buddy.ps1') @PSBoundParameters
exit $LASTEXITCODE
