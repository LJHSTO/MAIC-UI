$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$PidPath = Join-Path $ScriptDir "local-service-pids.json"

function Stop-ProcessTree {
  param([Parameter(Mandatory = $true)] [int]$ProcessId)

  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }

  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($process) {
    Write-Host "Stopping PID $ProcessId ($($process.ProcessName))"
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }
}

if (-not (Test-Path -LiteralPath $PidPath)) {
  Write-Host "No PID file found: $PidPath"
  Write-Host "Nothing to stop from this script."
  return
}

$items = Get-Content -LiteralPath $PidPath -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $items) {
  Write-Host "PID file is empty."
  return
}

if ($items -isnot [System.Array]) {
  $items = @($items)
}

foreach ($item in $items) {
  if ($item.pid) {
    Stop-ProcessTree -ProcessId ([int]$item.pid)
  }
}

Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
Write-Host "Stopped local services recorded in: $PidPath"
