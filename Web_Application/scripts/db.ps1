# Switch the active database profile: db.ps1 pi | local | status
param([Parameter(Mandatory)][ValidateSet('pi', 'local', 'status')][string]$Profile)

$root = Split-Path $PSScriptRoot -Parent
$active = Join-Path $root '.env.db'

function Show-Active {
    if (-not (Test-Path $active)) { Write-Host 'No active profile (using DB_* from .env).'; return }
    foreach ($name in 'pi', 'local') {
        $src = Join-Path $root ".env.db.$name"
        if ((Test-Path $src) -and ((Get-FileHash $src).Hash -eq (Get-FileHash $active).Hash)) { $label = $name }
    }
    if (-not $label) { $label = 'custom' }
    $dbHost = (Select-String -Path $active -Pattern '^DB_HOST=(.*)$').Matches.Groups[1].Value
    Write-Host "Active profile: $label (DB_HOST=$dbHost)"
}

if ($Profile -eq 'status') { Show-Active; exit 0 }

$source = Join-Path $root ".env.db.$Profile"
if (-not (Test-Path $source)) {
    Write-Error "Missing $source. Create it from .env.db.example first."
    exit 1
}
Copy-Item $source $active -Force
Show-Active
