<#
    Bring a machine from nothing to a demoable GlassBox.

        .\scripts\bootstrap.ps1              full rebuild
        .\scripts\bootstrap.ps1 -SkipDocker  Postgres is already up

    Run it from the repository root.
#>
[CmdletBinding()]
param(
    [switch] $SkipDocker,
    [switch] $SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

if (-not $SkipDocker) {
    Step 'Docker'
    docker info *> $null
    if (-not $?) {
        Write-Host 'Docker daemon is not running. Starting Docker Desktop...' -ForegroundColor Yellow
        $exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path $exe)) { throw "Docker Desktop not found at $exe" }
        Start-Process $exe
        $deadline = (Get-Date).AddMinutes(3)
        do {
            Start-Sleep -Seconds 5
            docker info *> $null
            $up = $?
        } while (-not $up -and (Get-Date) -lt $deadline)
        if (-not $up) { throw 'Docker did not become ready within three minutes.' }
    }
    docker compose up -d
    Write-Host 'waiting for Postgres to accept connections...'
    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 2
        $state = (docker inspect -f '{{.State.Health.Status}}' glassbox_pg 2>$null)
    } while ($state -ne 'healthy' -and (Get-Date) -lt $deadline)
    if ($state -ne 'healthy') { throw "Postgres container is '$state', not healthy." }
}

if (-not (Test-Path '.env')) {
    Step 'Configuration'
    Copy-Item '.env.example' '.env'
    Write-Host 'wrote .env from .env.example'
}

Step 'Python dependencies'
python -m pip install -q -r requirements.txt
# Editable install so `python -m glassbox ...` works from anywhere. The scripts
# under scripts/ add src/ to sys.path themselves and run without it.
python -m pip install -q -e .

Step 'Fixtures'
python scripts/generate_synthetic.py

Step 'Database — migrate, seed, load, compute features'
python scripts/reset_db.py

Step 'Decisioning — both lanes'
python scripts/run_cycle.py --lane inline_sync
python scripts/run_cycle.py --lane async

Step 'Published contract'
python scripts/export_contract_schema.py
python scripts/export_expectations.py

if (-not $SkipTests) {
    Step 'Acceptance tests'
    python -m pytest -q
}

Step 'Done'
Write-Host @'
Next:
  psql "$env:GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   human-readable proof
  python -m glassbox serve                                      read API on :8000
  curl http://127.0.0.1:8000/alerts
'@ -ForegroundColor Green
