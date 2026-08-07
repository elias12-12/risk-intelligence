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
    $oldErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    docker info *> $null
    $dockerOk = ($LASTEXITCODE -eq 0)

    $ErrorActionPreference = $oldErrorAction

    if (-not $dockerOk) {
        Write-Host 'Docker daemon is not running. Starting Docker Desktop...' -ForegroundColor Yellow
        $exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path $exe)) { throw "Docker Desktop not found at $exe" }
        Start-Process $exe
        $deadline = (Get-Date).AddMinutes(3)
        $ErrorActionPreference = 'Continue'
        do {
            Start-Sleep -Seconds 5
            docker info *> $null
            # $LASTEXITCODE, not $?: under ErrorActionPreference='Stop' a native
            # command writing to stderr makes $? unreliable, so the wait loop
            # could exit on the first poll and then fail on a healthy daemon.
            $up = ($LASTEXITCODE -eq 0)
        } while (-not $up -and (Get-Date) -lt $deadline)
        $ErrorActionPreference = $oldErrorAction
        if (-not $up) { throw 'Docker did not become ready within three minutes.' }
    }
    # Same trap as the `docker info` poll above, and it bit for real: under
    # ErrorActionPreference='Stop' a native command writing ANYTHING to stderr
    # becomes a terminating NativeCommandError, and `docker compose up -d` emits
    # `level=warning msg="No services to build"` on a perfectly healthy bring-up.
    # The container starts and the script dies. Check the exit code, which is
    # the only thing that actually reports failure here.
    $ErrorActionPreference = 'Continue'
    docker compose up -d
    $composeOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $oldErrorAction
    if (-not $composeOk) { throw "docker compose up -d failed with exit code $LASTEXITCODE." }

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

Step 'Actions — settle outcomes, disposition cases'
python scripts/resolve_actions.py

# The loop that closes: the system's own actions become an input to the next
# decision. challenge_failed events exist only after the step above, so the
# feature has to be recomputed before a cycle can read it.
Step 'Feature loop — the system reads its own actions'
python scripts/run_features.py --no-graph --feature card_challenge_fails_30d

Step 'Condition performance — §10'
python scripts/condition_report.py

Step 'Band calibration — §10 (recommends, never writes)'
python scripts/calibrate_bands.py

Step 'KPIs — §11'
python scripts/kpi_report.py

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
  python scripts/case_report.py --alert 5 --citations           the filing draft, sourced
  python -m glassbox serve                                      API on :8000, cycle every 30s
  curl http://127.0.0.1:8000/alerts                             alert.v1
  curl http://127.0.0.1:8000/queue                              queue.v1, priority-ordered
  curl http://127.0.0.1:8000/alerts/1/executions                executions.v1
  curl http://127.0.0.1:8000/kpis                               kpis.v1, nine tiles
  curl http://127.0.0.1:8000/alerts/5/copilot                   explanation.v1

The live demo — five charges arriving one at a time, on a device nobody has
seen. The first four are approved; the fifth scores 87 and is DECLINED with a
step-up. Nothing about it was in the fixtures.

  python scripts/demo_burst.py                                  ingest.v1, end to end
  python scripts/demo_burst.py --http                           through the running API

The same thing in a browser. Not run here — it builds a Node image, and this
script's promise is a demoable database in three minutes. Nothing above started
it, which is what the compose profile is for:

  docker compose --profile console up -d console                :5173, proxying /api
  docker compose --profile console run --rm console npm test    40 tests
  docker compose --profile console run --rm console npm run build   served at :8000/console

Node is not installed on this machine and none of those ask it to be. The
container reaches the API over the Docker bridge, so serve it as:

  $env:GLASSBOX_HOST='0.0.0.0'; python -m glassbox serve

A loopback bind refuses that connection, and the console reports it exactly as
it would report a service that is not running.

Sign in with analyst-token or admin-token. Set GLASSBOX_CYCLE_SECONDS
deliberately first: the default 30 makes rows appear that no click caused.
'@ -ForegroundColor Green
