#!/usr/bin/env pwsh
<#
.SYNOPSIS
  MAIC-UI batch course generator (PowerShell)
.DESCRIPTION
  Reads a TSV manifest and generates multiple courses sequentially.
  Calls maic_generate.ps1 for each task. Produces per-task output
  folders, per-task logs, and a summary file.
.PARAMETER Manifest
  Path to the TSV manifest. Default: examples\batch_courses.tsv
.EXAMPLE
  .\scripts\maic_batch.ps1
  .\scripts\maic_batch.ps1 examples\batch_courses.tsv
#>
param(
    [Parameter(Position=0)]
    [string]$Manifest = "examples\batch_courses.tsv"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $RootDir ".env.batch"

# ── helpers ──────────────────────────────────────────────
function Write-Step { param([string]$Msg) Write-Host ">> $Msg" -ForegroundColor Cyan }
function Write-OK   { param([string]$Msg) Write-Host "    OK  $Msg" -ForegroundColor Green }
function Write-Err  { param([string]$Msg) Write-Host "    ERR $Msg" -ForegroundColor Red }

if (-not (Test-Path $EnvFile)) {
    Write-Err ".env.batch not found. Run '.\scripts\maic_generate.ps1 setup' first."
    exit 1
}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.*)$') {
        $k = $matches[1].Trim(); $v = $matches[2].Trim()
        if ($v -match '^"(.*)"$' -or $v -match "^'(.*)'$") { $v = $matches[1] }
        [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}

$ApiBase        = $env:MAIC_API_BASE
$BatchOutDir    = if ($env:MAIC_BATCH_OUT_DIR) { $env:MAIC_BATCH_OUT_DIR } else { "maic_batch_outputs" }
$LogDir         = if ($env:MAIC_BATCH_LOG_DIR)  { $env:MAIC_BATCH_LOG_DIR }  else { Join-Path $BatchOutDir "logs" }
$ContinueOnErr  = if ($env:MAIC_CONTINUE_ON_ERROR -eq "false") { $false } else { $true }
$Generator      = Join-Path $ScriptDir "maic_generate.ps1"

if (-not (Test-Path $Generator)) {
    Write-Err "Generator not found: $Generator"
    exit 1
}

$manifestPath = $Manifest
if (-not [System.IO.Path]::IsPathRooted($Manifest)) {
    $manifestPath = Join-Path (Get-Location) $Manifest
}
if (-not (Test-Path $manifestPath)) {
    Write-Err "Manifest not found: $manifestPath"
    exit 1
}

$manifestDir = Split-Path -Parent (Resolve-Path $manifestPath)
New-Item -ItemType Directory -Force -Path $BatchOutDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host "`n=== MAIC-UI Batch Generation ===" -ForegroundColor Yellow
Write-Host "Manifest : $manifestPath"
Write-Host "Output   : $BatchOutDir"
Write-Host "Logs     : $LogDir"
Write-Host ""

# ── parse TSV ────────────────────────────────────────────
$total = 0; $ok = 0; $failed = 0
$tasks = @()

Get-Content $manifestPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }

    $cols = $line -split "\t"
    if ($cols.Count -lt 3) { return }

    $id   = $cols[0].Trim()
    $type = $cols[1].Trim()
    $input= $cols[2].Trim()
    if ($id -eq "id" -and $type -eq "type") { return }  # header row

    if (-not $id -or -not $type -or -not $input) {
        Write-Err "Skipping malformed line: $line"
        return
    }

    $title    = if ($cols.Count -ge 4) { $cols[3].Trim() } else { "" }
    $subject  = if ($cols.Count -ge 5) { $cols[4].Trim() } else { "" }
    $grade    = if ($cols.Count -ge 6) { $cols[5].Trim() } else { "" }
    $model    = if ($cols.Count -ge 7) { $cols[6].Trim() } else { "" }
    $public   = if ($cols.Count -ge 8) { $cols[7].Trim() } else { "" }
    $mode     = if ($cols.Count -ge 9) { $cols[8].Trim() } else { "" }
    $language = if ($cols.Count -ge 10){ $cols[9].Trim() } else { "" }

    # resolve relative input paths
    if (-not [System.IO.Path]::IsPathRooted($input)) {
        $input = Join-Path $manifestDir $input
    }

    $tasks += @{
        id = $id; type = $type; input = $input; title = $title
        subject = $subject; grade = $grade; model = $model
        public = $public; mode = $mode; language = $language
    }
}

if ($tasks.Count -eq 0) {
    Write-Err "No tasks found in manifest (or all are commented out)"
    exit 1
}

Write-Host "Tasks loaded: $($tasks.Count)`n"

# ── process tasks ────────────────────────────────────────
$startedAll = Get-Date

foreach ($task in $tasks) {
    $total++
    $taskId = $task.id
    $taskOutDir = Join-Path $BatchOutDir $taskId
    $logFile = Join-Path $LogDir "$taskId.log"
    New-Item -ItemType Directory -Force -Path $taskOutDir | Out-Null

    $ts = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    "[$ts] START id=$taskId type=$($task.type) input=$($task.input)" | Out-File $logFile -Encoding UTF8
    Write-Step "[$total/$($tasks.Count)] $taskId ($($task.type))"

    # build env overrides
    $envVars = @{}
    if ($task.subject)  { $envVars["MAIC_SUBJECT"] = $task.subject }
    if ($task.grade)    { $envVars["MAIC_GRADE"] = $task.grade }
    if ($task.model)    { $envVars["MAIC_MODEL"] = $task.model }
    if ($task.public)   { $envVars["MAIC_PUBLIC"] = $task.public }
    if ($task.mode)     { $envVars["MAIC_GENERATION_MODE"] = $task.mode }
    if ($task.language) { $envVars["MAIC_LANGUAGE"] = $task.language }
    $envVars["MAIC_OUT_DIR"] = $taskOutDir

    # save old env values and override
    $oldVals = @{}
    foreach ($kv in $envVars.GetEnumerator()) {
        $oldVals[$kv.Key] = [Environment]::GetEnvironmentVariable($kv.Key, "Process")
        [Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, "Process")
    }

    try {
        $genArgs = @($task.type, $task.input)
        if ($task.title -and $task.type -ne "concept") { $genArgs += $task.title }

        $result = & powershell -NoProfile -ExecutionPolicy Bypass -File $Generator @genArgs 2>&1
        $result | Out-File $logFile -Append -Encoding UTF8

        if ($LASTEXITCODE -eq 0) {
            $ok++
            $ts = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
            "[$ts] OK id=$taskId" | Out-File $logFile -Append -Encoding UTF8
            Write-OK "$taskId done"
        } else {
            $failed++
            $ts = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
            "[$ts] FAILED id=$taskId exit=$LASTEXITCODE" | Out-File $logFile -Append -Encoding UTF8
            Write-Err "$taskId FAILED (exit=$LASTEXITCODE)"
            if (-not $ContinueOnErr) { break }
        }
    } catch {
        $failed++
        $ts = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
        "[$ts] FAILED id=$taskId error=$($_.Exception.Message)" | Out-File $logFile -Append -Encoding UTF8
        Write-Err "$taskId FAILED: $($_.Exception.Message)"
        if (-not $ContinueOnErr) { break }
    } finally {
        # restore old env values
        foreach ($kv in $oldVals.GetEnumerator()) {
            if ($null -ne $kv.Value) {
                [Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, "Process")
            }
        }
    }
}

# ── summary ──────────────────────────────────────────────
$elapsed = ((Get-Date) - $startedAll).TotalSeconds
$summaryFile = Join-Path $BatchOutDir "summary.txt"
@"
completed_at=$(Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz")
manifest=$manifestPath
total=$total
ok=$ok
failed=$failed
elapsed_seconds=$elapsed
outputs=$BatchOutDir
logs=$LogDir
"@ | Set-Content $summaryFile -Encoding UTF8

Write-Host ""
Write-Host "=== Batch Complete ===" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })
Write-Host "Total: $total  OK: $ok  Failed: $failed  Elapsed: $([math]::Round($elapsed,0))s"
Write-Host "Summary: $summaryFile"
