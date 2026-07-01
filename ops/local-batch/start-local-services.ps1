param(
  [string]$MaicUiRoot = "",
  [string]$OpenMaicRoot = "",
  [switch]$SkipMaicBackend,
  [switch]$SkipMaicFrontend,
  [switch]$SkipOpenMaic
)

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\.."))
if (-not $MaicUiRoot) {
  $MaicUiRoot = $RepoRoot
}
if (-not $OpenMaicRoot) {
  $OpenMaicRoot = Join-Path (Split-Path -Parent $RepoRoot) "OpenMAIC"
}
if (-not (Test-Path -LiteralPath $OpenMaicRoot)) {
  Write-Warning "OpenMAIC root not found: $OpenMaicRoot. Skipping OpenMAIC startup."
  $SkipOpenMaic = $true
}
$LogDir = Join-Path $ScriptDir "logs"
$PidPath = Join-Path $ScriptDir "local-service-pids.json"

if (-not (Test-Path -LiteralPath $LogDir)) {
  New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Test-HttpReady {
  param([Parameter(Mandatory = $true)] [string]$Url)

  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
    return ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Wait-HttpReady {
  param(
    [Parameter(Mandatory = $true)] [string]$Name,
    [Parameter(Mandatory = $true)] [string]$Url,
    [int]$Seconds = 45
  )

  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    if (Test-HttpReady -Url $Url) {
      Write-Host "  OK: $Name is ready at $Url"
      return $true
    }
    Start-Sleep -Seconds 2
  } while ((Get-Date) -lt $deadline)

  Write-Warning "  $Name did not become ready within $Seconds seconds. Check logs."
  return $false
}

function Start-LocalProcess {
  param(
    [Parameter(Mandatory = $true)] [string]$Name,
    [Parameter(Mandatory = $true)] [string]$WorkingDirectory,
    [Parameter(Mandatory = $true)] [string]$Command,
    [Parameter(Mandatory = $true)] [string]$HealthUrl
  )

  if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
    throw "$Name working directory not found: $WorkingDirectory"
  }

  if (Test-HttpReady -Url $HealthUrl) {
    Write-Host "$Name already looks running: $HealthUrl"
    return $null
  }

  $stdoutPath = Join-Path $LogDir "$Name.out.log"
  $stderrPath = Join-Path $LogDir "$Name.err.log"

  Write-Host "Starting $Name ..."
  $process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
    -WorkingDirectory $WorkingDirectory `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru `
    -WindowStyle Hidden

  Wait-HttpReady -Name $Name -Url $HealthUrl | Out-Null

  return [PSCustomObject]@{
    name = $Name
    pid = $process.Id
    workingDirectory = $WorkingDirectory
    healthUrl = $HealthUrl
    stdout = $stdoutPath
    stderr = $stderrPath
    startedAt = (Get-Date).ToString("s")
  }
}

$started = @()

if (-not $SkipMaicBackend) {
  $backendDir = Join-Path $MaicUiRoot "backend"
  $cmd = '$env:PYTHONIOENCODING="utf-8"; python -m uvicorn main:app --host 127.0.0.1 --port 8000'
  $proc = Start-LocalProcess `
    -Name "maicui-backend" `
    -WorkingDirectory $backendDir `
    -Command $cmd `
    -HealthUrl "http://127.0.0.1:8000/health"
  if ($null -ne $proc) { $started += $proc }
}

if (-not $SkipMaicFrontend) {
  $frontendDir = Join-Path $MaicUiRoot "frontend"
  $cmd = '$env:NEXT_PUBLIC_API_URL="http://localhost:8000"; npm.cmd run dev -- --hostname localhost --port 3000'
  $proc = Start-LocalProcess `
    -Name "maicui-frontend" `
    -WorkingDirectory $frontendDir `
    -Command $cmd `
    -HealthUrl "http://localhost:3000"
  if ($null -ne $proc) { $started += $proc }
}

if (-not $SkipOpenMaic) {
  $cmd = 'corepack.cmd pnpm exec next dev --hostname localhost --port 3001'
  $proc = Start-LocalProcess `
    -Name "openmaic" `
    -WorkingDirectory $OpenMaicRoot `
    -Command $cmd `
    -HealthUrl "http://localhost:3001/api/health"
  if ($null -ne $proc) { $started += $proc }
}

$started | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $PidPath -Encoding UTF8

Write-Host ""
Write-Host "Local services:"
Write-Host "  MAIC-UI frontend: http://localhost:3000"
Write-Host "  MAIC-UI backend:  http://127.0.0.1:8000"
Write-Host "  OpenMAIC:         http://localhost:3001"
Write-Host ""
Write-Host "PID file: $PidPath"
Write-Host "Logs:     $LogDir"
