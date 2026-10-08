param(
    [switch]$SkipM365,
    [switch]$SkipPimalaya
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Has-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host "P4 Unified Communications dependency check"

if (-not $SkipM365) {
    if (Has-Command "m365") {
        Write-Host "  m365: already available"
    } elseif (Has-Command "npm") {
        Write-Host "  m365: installing @pnp/cli-microsoft365"
        npm install --global @pnp/cli-microsoft365
    } else {
        Write-Warning "  m365: npm was not found; install Node.js/npm, then run npm install --global @pnp/cli-microsoft365"
    }
}

if (-not $SkipPimalaya) {
    if (Has-Command "himalaya") {
        Write-Host "  himalaya: already available"
    } elseif (Has-Command "cargo") {
        Write-Host "  himalaya: installing from the upstream source"
        cargo install --locked --git https://github.com/pimalaya/himalaya.git
    } else {
        Write-Warning "  himalaya: not found; install a Windows release or Rust/cargo, then install Himalaya"
    }

    if (Has-Command "calendula") {
        Write-Host "  calendula: already available"
    } elseif (Has-Command "cargo") {
        Write-Host "  calendula: installing from the signed upstream source"
        cargo install --locked --git https://github.com/pimalaya/calendula.git
    } else {
        Write-Warning "  calendula: not found; install a Windows release or Rust/cargo, then install Calendula"
    }
}

$configPath = Join-Path $ProjectRoot "config.json"
$examplePath = Join-Path $ProjectRoot "config.example.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath $examplePath -Destination $configPath
    Write-Host "Created $configPath; edit it before making provider calls."
} else {
    Write-Host "Preserved existing $configPath"
}

Write-Host "Next steps:"
Write-Host "  1. Authenticate PnP CLI with: m365 login"
Write-Host "  2. Configure Himalaya and Calendula accounts"
Write-Host "  3. Edit config.json (especially Outlook username)"
Write-Host "  4. Run: python .\p4_comms.py --pretty health --probe"
