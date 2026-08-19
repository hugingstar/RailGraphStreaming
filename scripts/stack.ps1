<#
.SYNOPSIS
  Start / stop / inspect the RailGraph streaming stack.

.EXAMPLE
  ./scripts/stack.ps1 up       # postgres + kafka + dispatcher + estimator + persist + api
  ./scripts/stack.ps1 status
  ./scripts/stack.ps1 logs estimator
  ./scripts/stack.ps1 down
#>
param(
    [Parameter(Position = 0)][ValidateSet('up', 'down', 'restart', 'status', 'logs', 'topics')]
    [string]$Command = 'status',
    [Parameter(Position = 1)][string]$Service
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$LogDir = Join-Path $Root '.logs'
$Services = @(
    @{ Name = 'dispatcher'; Args = @('-m', 'railgraph.services.dispatcher') },
    @{ Name = 'estimator';  Args = @('-m', 'railgraph.services.estimator') },
    @{ Name = 'persist';    Args = @('-m', 'railgraph.services.persist') },
    @{ Name = 'graphbuild'; Args = @('-m', 'railgraph.services.graphbuild') },
    @{ Name = 'api';        Args = @('-m', 'uvicorn', 'railgraph.services.api:app',
                                     '--host', '0.0.0.0', '--port', '8123') }
)

function Get-RailProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*railgraph.services*' }
}

function Stop-Rail {
    $procs = Get-RailProcesses
    foreach ($p in $procs) {
        Write-Host "  stopping pid $($p.ProcessId)" -ForegroundColor DarkGray
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if (-not $procs) { Write-Host '  no python services running' -ForegroundColor DarkGray }
}

function Start-Rail {
    if (-not (Test-Path $Py)) { throw "venv missing: run  python -m venv backend/.venv  first" }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    Write-Host 'kafka...' -ForegroundColor Cyan
    Push-Location $Root
    try { docker compose up -d | Out-Null } finally { Pop-Location }

    for ($i = 0; $i -lt 40; $i++) {
        $health = docker inspect -f '{{.State.Health.Status}}' railgraph-kafka 2>$null
        if ($health -eq 'healthy') { break }
        Start-Sleep -Seconds 2
    }
    if ($health -ne 'healthy') { throw "kafka did not become healthy (last: $health)" }
    Write-Host "  kafka healthy on localhost:19092" -ForegroundColor Green

    $env:PYTHONIOENCODING = 'utf-8'
    foreach ($svc in $Services) {
        $log = Join-Path $LogDir "$($svc.Name).log"
        Start-Process -FilePath $Py -ArgumentList $svc.Args `
            -WorkingDirectory (Join-Path $Root 'backend') `
            -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
            -WindowStyle Hidden | Out-Null
        Write-Host "  started $($svc.Name) -> $log" -ForegroundColor Green
        Start-Sleep -Seconds 4          # let the dispatcher seed the log first
    }
    Write-Host ''
    Write-Host 'api      http://localhost:8123/api/stats' -ForegroundColor Yellow
    Write-Host 'kafka-ui http://localhost:8085' -ForegroundColor Yellow
}

switch ($Command) {
    'up'      { Start-Rail }
    'down'    { Stop-Rail; Push-Location $Root; try { docker compose down } finally { Pop-Location } }
    'restart' { Stop-Rail; Start-Sleep -Seconds 2; Start-Rail }
    'status'  {
        docker compose --project-directory $Root ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
        Write-Host ''
        $procs = Get-RailProcesses
        if ($procs) {
            $procs | ForEach-Object {
                $name = ($_.CommandLine -split 'railgraph.services.')[-1] -split ' ' | Select-Object -First 1
                '{0,-12} pid {1}' -f $name, $_.ProcessId
            }
        } else { Write-Host 'no python services running' }
        try {
            $s = Invoke-RestMethod http://localhost:8123/api/stats -TimeoutSec 3
            Write-Host ''
            '{0} active / {1} tracked | avg delay {2}s | {3} msg/s' -f `
                $s.active, $s.tracked, $s.avg_delay_s, $s.msgs_per_sec
        } catch { Write-Host "`napi not answering on :8123" -ForegroundColor DarkGray }
    }
    'logs'    {
        if (-not $Service) { throw 'usage: stack.ps1 logs <dispatcher|estimator|api>' }
        $err = Join-Path $LogDir "$Service.log.err"
        if ((Test-Path $err) -and (Get-Item $err).Length -gt 0) {
            Write-Host "--- stderr ---" -ForegroundColor DarkYellow
            Get-Content $err -Tail 20
            Write-Host "--- stdout ---" -ForegroundColor DarkYellow
        }
        Get-Content (Join-Path $LogDir "$Service.log") -Tail 40 -Wait
    }
    'topics'  {
        docker exec railgraph-kafka /opt/kafka/bin/kafka-topics.sh `
            --bootstrap-server localhost:9092 --describe
    }
}
