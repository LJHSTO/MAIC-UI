param(
  [string]$BaseUrl = "http://localhost:3001",
  [string]$CsvPath = (Join-Path $PSScriptRoot "openmaic-courses.csv"),
  [string]$OutputDir = (Join-Path $PSScriptRoot "outputs\openmaic"),
  [int]$PollSeconds = 15,
  [int]$TimeoutMinutes = 120,
  [string]$DefaultPdfProviderId = "unpdf",
  [switch]$NoPdfParse,
  [switch]$NoWait,
  [switch]$SaveClassroomJson
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Net.Http

function Get-Cell {
  param(
    [Parameter(Mandatory = $true)] $Row,
    [Parameter(Mandatory = $true)] [string]$Name,
    [string]$Default = ""
  )

  if ($Row.PSObject.Properties.Name -contains $Name) {
    $value = $Row.$Name
    if ($null -ne $value) {
      return ([string]$value).Trim()
    }
  }
  return $Default
}

function Convert-CellToBool {
  param([string]$Value, [bool]$Default = $false)

  $normalized = ($Value -as [string]).Trim().ToLowerInvariant()
  if ($normalized -in @("1", "true", "yes", "y", "on")) { return $true }
  if ($normalized -in @("0", "false", "no", "n", "off")) { return $false }
  return $Default
}

function Invoke-JsonRequest {
  param(
    [Parameter(Mandatory = $true)] [string]$Uri,
    [Parameter(Mandatory = $true)] [string]$Method,
    [object]$Body = $null
  )

  $params = @{
    Uri = $Uri
    Method = $Method
    Headers = @{ "Accept" = "application/json" }
  }

  if ($null -ne $Body) {
    $json = $Body | ConvertTo-Json -Depth 30
    $params.ContentType = "application/json; charset=utf-8"
    $params.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
  }

  return Invoke-RestMethod @params
}

function Resolve-InputFile {
  param(
    [Parameter(Mandatory = $true)] [string]$PathFromCsv,
    [Parameter(Mandatory = $true)] [string]$CsvDirectory
  )

  if ([System.IO.Path]::IsPathRooted($PathFromCsv)) {
    return [System.IO.Path]::GetFullPath($PathFromCsv)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $CsvDirectory $PathFromCsv))
}

function New-PdfPageSlice {
  param(
    [Parameter(Mandatory = $true)] [string]$SourcePath,
    [Parameter(Mandatory = $true)] [string]$PageRange,
    [Parameter(Mandatory = $true)] [string]$OutputDir,
    [Parameter(Mandatory = $true)] [int]$RowIndex
  )

  if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
  }

  $baseName = [System.IO.Path]::GetFileNameWithoutExtension($SourcePath)
  $safeRange = ($PageRange -replace "[^0-9,\-]", "_")
  $outputPath = Join-Path $OutputDir ("{0}-row{1:000}-pages-{2}.pdf" -f $baseName, $RowIndex, $safeRange)

  $pythonScript = @'
import re
import sys
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter

source = Path(sys.argv[1])
page_spec = sys.argv[2].strip()
target = Path(sys.argv[3])

def parse_pages(spec, total_pages):
    pages = []
    for part in re.split(r"[,;]", spec):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ValueError(f"Invalid page range part: {part!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1 or start > end:
            raise ValueError(f"Invalid page range: {part!r}")
        if end > total_pages:
            raise ValueError(f"Page range {part!r} exceeds PDF page count {total_pages}")
        pages.extend(range(start - 1, end))
    if not pages:
        raise ValueError("Empty page range")
    return pages

reader = PdfReader(str(source))
selected_pages = parse_pages(page_spec, len(reader.pages))
writer = PdfWriter()
for page_index in selected_pages:
    writer.add_page(reader.pages[page_index])

target.parent.mkdir(parents=True, exist_ok=True)
with target.open("wb") as fh:
    writer.write(fh)

print(str(target))
'@

  $createdPath = $pythonScript | python - $SourcePath $PageRange $outputPath
  if (-not (Test-Path -LiteralPath $createdPath)) {
    throw "Failed to create PDF page slice: $createdPath"
  }
  return [System.IO.Path]::GetFullPath($createdPath)
}

function Send-OpenMaicPdfParse {
  param(
    [Parameter(Mandatory = $true)] [string]$Uri,
    [Parameter(Mandatory = $true)] [string]$FilePath,
    [Parameter(Mandatory = $true)] [string]$ProviderId,
    [string]$ApiKey = "",
    [string]$ProviderBaseUrl = "",
    [int]$TimeoutMinutes = 120
  )

  $handler = New-Object System.Net.Http.HttpClientHandler
  $client = New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout = [TimeSpan]::FromMinutes([Math]::Max($TimeoutMinutes, 5))
  $content = New-Object System.Net.Http.MultipartFormDataContent
  $fileStream = $null

  try {
    $content.Add((New-Object System.Net.Http.StringContent($ProviderId, [Text.Encoding]::UTF8)), "providerId")
    if ($ApiKey) {
      $content.Add((New-Object System.Net.Http.StringContent($ApiKey, [Text.Encoding]::UTF8)), "apiKey")
    }
    if ($ProviderBaseUrl) {
      $content.Add((New-Object System.Net.Http.StringContent($ProviderBaseUrl, [Text.Encoding]::UTF8)), "baseUrl")
    }

    $fileStream = [System.IO.File]::OpenRead($FilePath)
    $filePart = New-Object System.Net.Http.StreamContent($fileStream)
    $filePart.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("application/pdf")
    $content.Add($filePart, "pdf", [System.IO.Path]::GetFileName($FilePath))

    $response = $client.PostAsync($Uri, $content).GetAwaiter().GetResult()
    $responseText = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
      throw "HTTP $([int]$response.StatusCode): $responseText"
    }

    $json = $responseText | ConvertFrom-Json
    if (-not $json.success -or -not $json.data) {
      throw "PDF parse response did not contain data: $responseText"
    }
    return $json.data
  } finally {
    if ($null -ne $content) { $content.Dispose() }
    if ($null -ne $fileStream) { $fileStream.Dispose() }
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $handler) { $handler.Dispose() }
  }
}

function Build-RequirementFromRow {
  param([Parameter(Mandatory = $true)] $Row)

  $explicit = Get-Cell $Row "requirement"
  if ($explicit) { return $explicit }

  $parts = New-Object System.Collections.Generic.List[string]
  foreach ($pair in @(
    @("title", "Course title"),
    @("subject", "Subject"),
    @("grade_level", "Grade level"),
    @("description", "Generation requirement"),
    @("lesson_id", "Lesson id"),
    @("source_chapter", "Source chapter"),
    @("source_section", "Source section"),
    @("page_range", "PDF page range"),
    @("concept_name", "Core concept"),
    @("concept_overview", "Concept overview"),
    @("mastery_points", "Mastery goals"),
    @("design_idea", "Interactive design"),
    @("assessment_focus", "Assessment focus"),
    @("tracking_plan", "Tracking plan")
  )) {
    $value = Get-Cell $Row $pair[0]
    if ($value) {
      $parts.Add("$($pair[1]): $value") | Out-Null
    }
  }

  if ($parts.Count -eq 0) {
    return ""
  }

  return ($parts -join [Environment]::NewLine)
}

function Save-Results {
  param(
    [Parameter(Mandatory = $true)] [array]$Rows,
    [Parameter(Mandatory = $true)] [string]$Path
  )

  $Rows | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
}

$BaseUrl = $BaseUrl.TrimEnd("/")

if (-not (Test-Path -LiteralPath $CsvPath)) {
  throw "CSV file not found: $CsvPath"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
  New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$csvFullPath = [System.IO.Path]::GetFullPath($CsvPath)
$csvDirectory = Split-Path -Parent $csvFullPath
$rows = Import-Csv -LiteralPath $csvFullPath -Encoding UTF8
if (-not $rows -or $rows.Count -eq 0) {
  throw "CSV has no rows: $csvFullPath"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resultPath = Join-Path $OutputDir "openmaic-batch-results-$timestamp.csv"
$sliceDir = Join-Path $OutputDir "pdf-page-slices-$timestamp"
$jsonDir = Join-Path $OutputDir "classroom-json-$timestamp"
$results = @()

Write-Host ""
Write-Host "OpenMAIC local batch"
Write-Host "Base URL: $BaseUrl"
Write-Host "CSV:      $csvFullPath"
Write-Host "Rows:     $($rows.Count)"
Write-Host "Results:  $resultPath"
Write-Host "Mode:     sequential"
Write-Host ""

$previousCourses = @()

$index = 0
foreach ($row in $rows) {
  $index += 1

  $title = Get-Cell $row "title"
  $rawFile = Get-Cell $row "file"
  $pageRange = Get-Cell $row "page_range"
  $requirement = Build-RequirementFromRow -Row $row
  $status = "submitting"
  $errorText = ""
  $jobId = ""
  $classroomId = ""
  $classroomUrl = ""
  $jsonPath = ""
  $pdfTextLength = 0
  $pdfImageCount = 0

  if (-not $title) {
    $title = Get-Cell $row "lesson_id"
  }
  if (-not $title -and $rawFile) {
    $title = [System.IO.Path]::GetFileNameWithoutExtension($rawFile)
  }

  if ($previousCourses.Count -gt 0) {
    $prevSummary = "\n\n---\nPrevious courses in this batch (avoid repeating these concepts; you may briefly recall them in the pre-test):\n"
    for ($pi = 0; $pi -lt $previousCourses.Count; $pi++) {
      $prevSummary += ($pi + 1).ToString() + ". " + $previousCourses[$pi] + "\n"
    }
    $requirement = $requirement + $prevSummary.TrimEnd()
  }

  try {
    if (-not $requirement) {
      throw "Row $index has no requirement and no usable prompt fields."
    }

    $pdfContent = $null
    if ($rawFile -and -not $NoPdfParse) {
      $filePath = Resolve-InputFile -PathFromCsv $rawFile -CsvDirectory $csvDirectory
      if (-not (Test-Path -LiteralPath $filePath)) {
        throw "PDF file not found: $filePath"
      }

      $uploadFilePath = $filePath
      if ($pageRange) {
        $uploadFilePath = New-PdfPageSlice `
          -SourcePath $filePath `
          -PageRange $pageRange `
          -OutputDir $sliceDir `
          -RowIndex $index
        Write-Host "[$index/$($rows.Count)] Page slice: $pageRange -> $uploadFilePath"
      }

      $providerId = Get-Cell $row "pdf_provider_id" $DefaultPdfProviderId
      $providerApiKey = Get-Cell $row "pdf_api_key"
      $providerBaseUrl = Get-Cell $row "pdf_base_url"

      Write-Host "[$index/$($rows.Count)] Parsing PDF for OpenMAIC: $title"
      $parsedPdf = Send-OpenMaicPdfParse `
        -Uri "$BaseUrl/api/parse-pdf" `
        -FilePath $uploadFilePath `
        -ProviderId $providerId `
        -ApiKey $providerApiKey `
        -ProviderBaseUrl $providerBaseUrl `
        -TimeoutMinutes $TimeoutMinutes

      $images = @()
      if ($parsedPdf.images) {
        $images = @($parsedPdf.images)
      }

      $pdfTextLength = ([string]$parsedPdf.text).Length
      $pdfImageCount = $images.Count
      $pdfContent = @{
        text = [string]$parsedPdf.text
        images = $images
      }
    }

    $body = @{
      requirement = $requirement
      enableWebSearch = Convert-CellToBool (Get-Cell $row "enable_web_search") $false
      enableImageGeneration = Convert-CellToBool (Get-Cell $row "enable_image_generation") $false
      enableVideoGeneration = Convert-CellToBool (Get-Cell $row "enable_video_generation") $false
      enableTTS = Convert-CellToBool (Get-Cell $row "enable_tts") $false
    }

    $agentMode = Get-Cell $row "agent_mode"
    if ($agentMode) {
      $body["agentMode"] = $agentMode
    }
    if ($null -ne $pdfContent) {
      $body["pdfContent"] = $pdfContent
    }

    Write-Host "[$index/$($rows.Count)] Submitting OpenMAIC job: $title"
    $submit = Invoke-JsonRequest -Uri "$BaseUrl/api/generate-classroom" -Method "Post" -Body $body
    if (-not $submit.success -or -not $submit.jobId) {
      throw "OpenMAIC job submit failed."
    }

    $jobId = $submit.jobId
    Write-Host "  Job ID: $jobId"

    if ($NoWait) {
      $status = "submitted"
    } else {
      $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
      do {
        Start-Sleep -Seconds $PollSeconds
        $job = Invoke-JsonRequest -Uri "$BaseUrl/api/generate-classroom/$jobId" -Method "Get"
        $status = $job.status
        Write-Host "  Status: $status / $($job.step) / $($job.progress)% / scenes $($job.scenesGenerated)/$($job.totalScenes)"

        if ($job.done) {
          if ($job.status -eq "succeeded") {
            $classroomId = $job.result.classroomId
            $classroomUrl = $job.result.url
          } else {
            $errorText = $job.error
          }
          break
        }
      } while ((Get-Date) -lt $deadline)

      if (-not $status -or ($status -ne "succeeded" -and $status -ne "failed")) {
        $status = "timeout"
        $errorText = "Timed out after $TimeoutMinutes minutes"
      }

      if ($status -eq "succeeded" -and $SaveClassroomJson -and $classroomId) {
        if (-not (Test-Path -LiteralPath $jsonDir)) {
          New-Item -ItemType Directory -Path $jsonDir -Force | Out-Null
        }
        $classroom = Invoke-JsonRequest -Uri "$BaseUrl/api/classroom?id=$([uri]::EscapeDataString($classroomId))" -Method "Get"
        $safeName = ($title -replace '[\\/:*?"<>|]', '_')
        $jsonPath = Join-Path $jsonDir "$classroomId-$safeName.json"
        ($classroom | ConvertTo-Json -Depth 80) | Set-Content -LiteralPath $jsonPath -Encoding UTF8
      }
    }
  } catch {
    $status = "failed"
    $errorText = $_.Exception.Message
    Write-Warning "  Failed: $errorText"
  }

  $results += [PSCustomObject]@{
    row = $index
    title = $title
    file = $rawFile
    page_range = $pageRange
    status = $status
    job_id = $jobId
    classroom_id = $classroomId
    classroom_url = $classroomUrl
    pdf_text_length = $pdfTextLength
    pdf_image_count = $pdfImageCount
    json_path = $jsonPath
    error = $errorText
  }
  $curRequirement = Get-Cell $row "requirement"
  $curConcept = ""
  if ($curRequirement) {
    $conceptMatch = [regex]::Match($curRequirement, "\u6838\u5fc3\u77e5\u8bc6\u70b9\uff1a(.+?)(?:\n|$)")
    if ($conceptMatch.Success) { $curConcept = $conceptMatch.Groups[1].Value.Trim() }
  }
  if ($curConcept) {
    $previousCourses += ($title + " - " + $curConcept)
  } else {
    $previousCourses += $title
  }
  Save-Results -Rows $results -Path $resultPath
  Write-Host ""
}

Write-Host "Done."
Write-Host "Results saved to: $resultPath"
