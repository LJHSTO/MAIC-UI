#!/usr/bin/env pwsh
<#
.SYNOPSIS
  MAIC-UI single course generator (PowerShell)
.DESCRIPTION
  Generates one learning course from a PDF, PPTX, or concept JSON.
  Also doubles as the one-time setup tool when called with "setup".
.PARAMETER Command
  setup  - Register account & test backend connectivity
  pdf    - Upload PDF and generate course
  ppt    - Upload PPTX/PDF slides and generate course
  concept - Upload concept JSON and generate course
.PARAMETER InputPath
  File path to the PDF, PPTX, or concept JSON.
.PARAMETER Title
  Course title (optional; defaults to filename stem).
.EXAMPLE
  .\scripts\maic_generate.ps1 setup
  .\scripts\maic_generate.ps1 concept examples\concept_derivative.json
  .\scripts\maic_generate.ps1 pdf .\lesson.pdf "Quadratic Functions"
#>
param(
    [Parameter(Position=0)]
    [ValidateSet("setup","pdf","ppt","concept")]
    [string]$Command,

    [Parameter(Position=1)]
    [string]$InputPath,

    [Parameter(Position=2)]
    [string]$Title
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $RootDir ".env.batch"

# ── helpers ──────────────────────────────────────────────
function Write-Step { param([string]$Msg) Write-Host ">> $Msg" -ForegroundColor Cyan }
function Write-OK   { param([string]$Msg) Write-Host "    OK  $Msg" -ForegroundColor Green }
function Write-Err  { param([string]$Msg) Write-Host "    ERR $Msg" -ForegroundColor Red }
function Write-Warn { param([string]$Msg) Write-Host "    WRN $Msg" -ForegroundColor Yellow }

function Load-Env {
    if (-not (Test-Path $EnvFile)) {
        Write-Err ".env.batch not found at $EnvFile"
        Write-Host "    Run '.\scripts\maic_generate.ps1 setup' first, or copy .env.batch.example to .env.batch and edit it."
        exit 1
    }
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.*)$') {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            if ($val -match '^"(.*)"$' -or $val -match "^'(.*)'$") { $val = $matches[1] }
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

function Resolve-PathSafe {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    $resolved = Join-Path (Get-Location) $Path
    if (-not (Test-Path $resolved)) { return $resolved }
    return (Resolve-Path $resolved).Path
}

function Invoke-Api {
    param(
        [string]$Method = "Get",
        [string]$Path,
        [hashtable]$Body,
        [hashtable]$Form,
        [int]$TimeoutSec = 300
    )
    $uri = "$ApiBase$Path"
    $headers = @{}
    if ($script:Token) { $headers["Authorization"] = "Bearer $script:Token" }

    $params = @{ Uri = $uri; Method = $Method; Headers = $headers; TimeoutSec = $TimeoutSec }
    if ($Body) { $params.Body = ($Body | ConvertTo-Json -Depth 10 -Compress); $params.ContentType = "application/json" }
    if ($Form) { $params.Form = $Form }

    try {
        $result = Invoke-RestMethod @params
        return $result
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        $reader.Close()
        throw "HTTP $statusCode : $responseBody"
    }
}

function Save-PdfOutputs {
    param([string]$DocId)
    $out = Join-Path $OutDir "pdf_$DocId"
    New-Item -ItemType Directory -Force -Path $out | Out-Null

    $doc = Invoke-Api -Path "/pdf/documents/$DocId"
    $doc | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $out "document.json") -Encoding UTF8

    $website = Invoke-Api -Path "/pdf/documents/$DocId/website"
    $website | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $out "website.json") -Encoding UTF8

    if ($website.html) {
        Set-Content (Join-Path $out "website.html") $website.html -Encoding UTF8
    }
    Write-OK "Saved to $out"
}

function Save-PptOutputs {
    param([string]$DocId)
    $out = Join-Path $OutDir "ppt_$DocId"
    New-Item -ItemType Directory -Force -Path $out | Out-Null

    $doc = Invoke-Api -Path "/ppt/documents/$DocId"
    $doc | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $out "document.json") -Encoding UTF8

    $view = Invoke-Api -Path "/ppt/documents/$DocId/interactive-view"
    $view | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $out "interactive-view.json") -Encoding UTF8

    $i = 1
    foreach ($item in $view.items) {
        if ($item.html) {
            $name = "item_{0:D3}_{1}_slide_{2}.html" -f $i, ($item.type -replace '[^a-zA-Z0-9]','_'), ($item.slide_number -replace '[^0-9]','')
            Set-Content (Join-Path $out $name) $item.html -Encoding UTF8
        }
        $i++
    }
    Write-OK "Saved to $out"
}

function Poll-UntilReady {
    param(
        [string]$Kind,
        [string]$DocId
    )
    $endpoint = if ($Kind -eq "ppt") { "/ppt/documents/$DocId/status" } else { "/pdf/documents/$DocId/processing-status" }
    $started = Get-Date

    while ($true) {
        $statusObj = Invoke-Api -Path $endpoint
        $st = if ($statusObj.status) { $statusObj.status } else { "unknown" }
        $pg = if ($statusObj.progress) { $statusObj.progress } else { "?" }
        $msg = if ($statusObj.message) { ": $($statusObj.message)" } else { "" }
        Write-Host "    document=$DocId status=$st progress=${pg}%$msg"

        switch ($st) {
            "ready"   { return $true }
            "error"   { Write-Err ($statusObj | ConvertTo-Json); return $false }
            "failed"  { Write-Err ($statusObj | ConvertTo-Json); return $false }
            "awaiting_template_selection" { Write-Err "Document needs manual template configuration. Set MAIC_USE_TEMPLATES=false."; return $false }
            "uploaded" { Write-Err "Document needs template configuration. Set MAIC_USE_TEMPLATES=false."; return $false }
        }

        if (((Get-Date) - $started).TotalSeconds -gt $Timeout) {
            Write-Err "Timed out after ${Timeout}s"
            return $false
        }
        Start-Sleep -Seconds 5
    }
}

# ── commands ──────────────────────────────────────────────

function Invoke-Setup {
    Write-Host "`n=== MAIC-UI Setup ===`n" -ForegroundColor Yellow

    # 0. Check .env.batch
    if (-not (Test-Path $EnvFile)) {
        $template = Join-Path $RootDir ".env.batch.example"
        if (-not (Test-Path $template)) {
            Write-Err ".env.batch.example not found"
            exit 1
        }
        Copy-Item $template $EnvFile
        Write-Warn "Created $EnvFile from template."
        Write-Host "    Please edit it and set MAIC_API_BASE to the host machine's LAN IP."
        Write-Host "    Then re-run: .\scripts\maic_generate.ps1 setup"
        exit 0
    }

    Load-Env
    $global:ApiBase = $env:MAIC_API_BASE
    if (-not $ApiBase) {
        Write-Err "MAIC_API_BASE is not set in .env.batch"
        exit 1
    }
    Write-Step "Testing backend at $ApiBase ..."
    try {
        Invoke-Api -Path "/auth/verify-token" -Method Options -TimeoutSec 5 | Out-Null
        Write-OK "Backend reachable"
    } catch {
        try {
            Invoke-Api -Path "/pdf/documents" -Method Options -TimeoutSec 5 | Out-Null
            Write-OK "Backend reachable"
        } catch {
            Write-Err "Cannot reach backend. Make sure it's running: uvicorn main:app --host 0.0.0.0 --port 8000"
            exit 1
        }
    }

    # 1. Check if account exists by trying login
    $Email = $env:MAIC_EMAIL
    $Password = $env:MAIC_PASSWORD
    $needsRegister = $false

    if ([string]::IsNullOrWhiteSpace($Email) -or $Email -eq "test@example.com") {
        Write-Step "No real account in .env.batch. Let's create one."
        $Email = Read-Host "    Email"
        $Username = Read-Host "    Username (3-20 chars)"
        $securePw = Read-Host "    Password (8+ chars, letters+digits)" -AsSecureString
        $Password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePw)
        )
        $needsRegister = $true
    } else {
        Write-Step "Testing login for $Email ..."
        try {
            $loginResp = Invoke-Api -Method Post -Path "/auth/login" -Body @{ email = $Email; password = $Password }
            Write-OK "Login OK (user: $($loginResp.user.username))"
            $script:Token = $loginResp.access_token
        } catch {
            if ($_.Exception.Message -match "Invalid email or password") {
                Write-Warn "Login failed. Trying registration..."
                $needsRegister = $true
            } else {
                throw
            }
        }
    }

    if ($needsRegister) {
        try {
            $regResp = Invoke-Api -Method Post -Path "/auth/register" -Body @{
                email = $Email; username = $Username; password = $Password
            }
            Write-OK "Registered: $Email ($($regResp.user.username))"
            $script:Token = $regResp.access_token
        } catch {
            if ($_.Exception.Message -match "already") {
                Write-OK "Account already exists, trying login..."
                $loginResp = Invoke-Api -Method Post -Path "/auth/login" -Body @{ email = $Email; password = $Password }
                $script:Token = $loginResp.access_token
                Write-OK "Login OK"
            } else {
                throw
            }
        }
    }

    # 2. Save credentials back to .env.batch
    $envContent = Get-Content $EnvFile
    $envContent = $envContent -replace '^MAIC_EMAIL=.*', "MAIC_EMAIL=$Email"
    $envContent = $envContent -replace '^MAIC_PASSWORD=.*', "MAIC_PASSWORD=$Password"
    $envContent | Set-Content $EnvFile -Encoding UTF8
    Write-OK "Credentials saved to .env.batch"

    Write-Host "`n=== Setup Complete ===`n" -ForegroundColor Green
    Write-Host "Next: .\scripts\maic_generate.ps1 concept examples\concept_derivative.json"
}

function Invoke-Pdf {
    if (-not $InputPath) { Write-Err "Usage: maic_generate.ps1 pdf <file.pdf> [title]"; exit 1 }
    $file = Resolve-PathSafe $InputPath
    if (-not (Test-Path $file)) { Write-Err "File not found: $file"; exit 1 }
    if (-not $Title) { $Title = [System.IO.Path]::GetFileNameWithoutExtension($file) }

    Load-Globals
    Do-Login

    $prefs = @{
        grade_level = if ($Subject) { [int]$Subject } else { $null }
        interests = @()
        include_exercises = $true
        include_prerequisites = $true
        language = $Language
    } | ConvertTo-Json -Depth 3 -Compress

    $form = @{
        file = Get-Item -Path $file
        title = $Title
        is_public = $IsPublic
        generation_mode = $GenerationMode
        ai_model = $Model
        user_preferences = $prefs
    }
    if ($Subject) { $form.subject = $Subject }
    if ($Grade)    { $form.grade_level = $Grade }
    if ($Description) { $form.description = $Description }

    Write-Step "Uploading PDF: $file"
    $resp = Invoke-Api -Method Post -Path "/pdf/upload" -Form $form
    $docId = $resp.id
    if (-not $docId) { Write-Err "Upload failed: $($resp | ConvertTo-Json)"; exit 1 }
    Write-OK "Document created: id=$docId"

    if (-not (Poll-UntilReady -Kind "pdf" -DocId $docId)) { exit 1 }
    Save-PdfOutputs -DocId $docId
}

function Invoke-Ppt {
    if (-not $InputPath) { Write-Err "Usage: maic_generate.ps1 ppt <file.pptx|file.pdf> [title]"; exit 1 }
    $file = Resolve-PathSafe $InputPath
    if (-not (Test-Path $file)) { Write-Err "File not found: $file"; exit 1 }
    if (-not $Title) { $Title = [System.IO.Path]::GetFileNameWithoutExtension($file) }

    $ext = ([System.IO.Path]::GetExtension($file)).ToLower()
    $fileType = if ($ext -eq ".pptx") { "pptx" } else { "pdf" }

    Load-Globals
    Do-Login

    $form = @{
        file = Get-Item -Path $file
        title = $Title
        file_type = $fileType
        is_public = $IsPublic
        auto_process = "false"
        ai_model = $Model
    }
    if ($Subject) { $form.subject = $Subject }
    if ($Grade)    { $form.grade_level = $Grade }
    if ($Description) { $form.description = $Description }

    Write-Step "Uploading PPT: $file"
    $resp = Invoke-Api -Method Post -Path "/ppt/upload" -Form $form
    $docId = $resp.id
    if (-not $docId) { Write-Err "Upload failed: $($resp | ConvertTo-Json)"; exit 1 }
    Write-OK "Document created: id=$docId"

    Write-Step "Configuring batch processing..."
    $configForm = @{
        mode = "batch"
        batch_size = "$PptBatchSize"
        use_templates = "$UseTemplates"
        ai_model = $Model
    }
    Invoke-Api -Method Post -Path "/ppt/documents/$docId/configure" -Form $configForm | Out-Null
    Write-OK "Configured"

    if (-not (Poll-UntilReady -Kind "ppt" -DocId $docId)) { exit 1 }
    Save-PptOutputs -DocId $docId
}

function Invoke-Concept {
    if (-not $InputPath) { Write-Err "Usage: maic_generate.ps1 concept <concept.json>"; exit 1 }
    $configFile = Resolve-PathSafe $InputPath
    if (-not (Test-Path $configFile)) { Write-Err "Concept JSON not found: $configFile"; exit 1 }

    Load-Globals
    Do-Login

    $config = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json

    $form = @{
        subject = $config.subject
        concept_name = $config.concept_name
        concept_overview = $config.concept_overview
        mastery_points = $config.mastery_points
        design_idea = $config.design_idea
        is_public = $IsPublic
        ai_model = $Model
        language = $Language
    }
    if ($config.grade_level)          { $form.grade_level = [string]$config.grade_level }
    if ($config.description)          { $form.description = $config.description }
    if ($config.interests)            { $form.interests = "$($config.interests)" }
    if ($null -ne $config.include_exercises)     { $form.include_exercises = [string]$config.include_exercises }
    if ($null -ne $config.include_prerequisites) { $form.include_prerequisites = [string]$config.include_prerequisites }

    Write-Step "Uploading concept: $($config.concept_name)"
    $resp = Invoke-Api -Method Post -Path "/pdf/concept/upload" -Form $form
    $docId = $resp.id
    if (-not $docId) { Write-Err "Upload failed: $($resp | ConvertTo-Json)"; exit 1 }
    Write-OK "Document created: id=$docId"

    if (-not (Poll-UntilReady -Kind "pdf" -DocId $docId)) { exit 1 }
    Save-PdfOutputs -DocId $docId
}

# ── shared state ──────────────────────────────────────────
$script:Token = $null

function Load-Globals {
    Load-Env
    $global:ApiBase        = $env:MAIC_API_BASE
    $global:Email          = $env:MAIC_EMAIL
    $global:Password       = $env:MAIC_PASSWORD
    $global:Model          = if ($env:MAIC_MODEL)          { $env:MAIC_MODEL }          else { "glm-4.7" }
    $global:Subject        = if ($env:MAIC_SUBJECT)        { $env:MAIC_SUBJECT }        else { "" }
    $global:Grade          = if ($env:MAIC_GRADE)          { $env:MAIC_GRADE }          else { "" }
    $global:Language       = if ($env:MAIC_LANGUAGE)       { $env:MAIC_LANGUAGE }       else { "zh" }
    $global:GenerationMode = if ($env:MAIC_GENERATION_MODE){ $env:MAIC_GENERATION_MODE} else { "heavy" }
    $global:IsPublic       = if ($env:MAIC_PUBLIC -eq "true") { "true" } else { "false" }
    $global:OutDir         = if ($env:MAIC_OUT_DIR)        { $env:MAIC_OUT_DIR }        else { "maic_outputs" }
    $global:Timeout        = if ($env:MAIC_TIMEOUT_SECONDS){ [int]$env:MAIC_TIMEOUT_SECONDS } else { 1800 }
    $global:PptBatchSize   = if ($env:MAIC_PPT_BATCH_SIZE) { $env:MAIC_PPT_BATCH_SIZE } else { "5" }
    $global:UseTemplates   = if ($env:MAIC_USE_TEMPLATES -eq "true") { "true" } else { "false" }
    $global:Description    = if ($env:MAIC_DESCRIPTION)    { $env:MAIC_DESCRIPTION }    else { "" }
    $global:Interests      = if ($env:MAIC_INTERESTS)      { $env:MAIC_INTERESTS }      else { "" }
    $global:IncludeExercises     = if ($env:MAIC_INCLUDE_EXERCISES -eq "false") { "false" } else { "true" }
    $global:IncludePrerequisites = if ($env:MAIC_INCLUDE_PREREQUISITES -eq "false") { "false" } else { "true" }
}

function Do-Login {
    Write-Step "Logging in as $Email ..."
    $loginResp = Invoke-Api -Method Post -Path "/auth/login" -Body @{ email = $Email; password = $Password }
    $script:Token = $loginResp.access_token
    Write-OK "Logged in (user: $($loginResp.user.username))"
}

# ── dispatch ──────────────────────────────────────────────
if (-not $Command) {
    Write-Host "Usage: .\scripts\maic_generate.ps1 [setup|pdf|ppt|concept] <args>`n"
    Write-Host "  setup     Register account & test connectivity"
    Write-Host "  concept   Generate from concept JSON"
    Write-Host "  pdf       Generate from PDF file"
    Write-Host "  ppt       Generate from PPTX or PDF slides"
    exit 1
}

switch ($Command) {
    "setup"   { Invoke-Setup }
    "pdf"     { Invoke-Pdf }
    "ppt"     { Invoke-Ppt }
    "concept" { Invoke-Concept }
}
