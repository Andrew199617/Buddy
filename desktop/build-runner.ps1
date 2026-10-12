<#
Builds resources\runner\buddy-runner.exe from backend\buddy_runner with PyInstaller,
so the installed app runs the Runner without Python on the computer.

    .\build-runner.ps1 [-Python <python 3.11+>]

The build uses its own virtual environment in .runner-build and leaves the
repository's environments alone. The result is a folder (buddy-runner.exe and
its _internal files), which electron-builder copies into the installer.
#>
param(
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'

$desktop = $PSScriptRoot
$repository = Split-Path -Parent $desktop
$buildRoot = Join-Path $desktop '.runner-build'
$environment = Join-Path $buildRoot 'venv'
$buildPython = Join-Path $environment 'Scripts\python.exe'
$output = Join-Path $desktop 'resources\runner'

# pip and PyInstaller log to stderr, which Windows PowerShell would otherwise treat
# as a failure; the exit code decides instead.
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Program @Arguments 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Program exited with $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $buildPython)) {
    Invoke-Checked $Python @('-m', 'venv', $environment)
}
Invoke-Checked $buildPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '-r', (Join-Path $desktop 'runner-requirements.txt'))

$work = Join-Path $buildRoot 'work'
$dist = Join-Path $buildRoot 'dist'
Invoke-Checked $buildPython @(
    '-m', 'PyInstaller',
    '--noconfirm', '--clean',
    '--name', 'buddy-runner',
    # A console program: the app reads its status lines and hides its window.
    '--console',
    '--paths', (Join-Path $repository 'backend'),
    '--collect-submodules', 'buddy_runner',
    '--workpath', $work,
    '--distpath', $dist,
    '--specpath', $buildRoot,
    (Join-Path $desktop 'runner-entry.py')
)

if (Test-Path -LiteralPath $output) {
    Remove-Item -LiteralPath $output -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
Copy-Item -LiteralPath (Join-Path $dist 'buddy-runner') -Destination $output -Recurse

$runner = Join-Path $output 'buddy-runner.exe'
$help = & $runner run --help
if ($LASTEXITCODE -ne 0 -or -not ($help -match '--connection-stdin')) {
    throw 'The built Runner did not start.'
}
"Built $runner"
