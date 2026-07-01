param(
  [string]$BaseUrl = "http://127.0.0.1:8000/api",
  [string]$CsvPath = (Join-Path $PSScriptRoot "courses.csv"),
  [string]$OutputDir = (Join-Path $PSScriptRoot "outputs"),
  [string]$Email = "",
  [string]$Password = "",
  [int]$PollSeconds = 30,
  [int]$TimeoutMinutes = 90,
  [switch]$DownloadHtml,
  [switch]$NoWait
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

function Convert-CellToBoolString {
  param([string]$Value, [string]$Default = "false")

  $normalized = ($Value -as [string]).Trim().ToLowerInvariant()
  if ($normalized -in @("1", "true", "yes", "y", "public")) {
    return "true"
  }
  if ($normalized -in @("0", "false", "no", "n", "private")) {
    return "false"
  }
  return $Default
}

function Convert-SecureStringToPlainText {
  param([Security.SecureString]$Secure)

  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

function Convert-ToSafeFileName {
  param(
    [Parameter(Mandatory = $true)] [string]$Value,
    [string]$Default = "untitled"
  )

  $safe = ($Value -replace '[\\/:*?"<>|]', '_').Trim()
  $safe = ($safe -replace '\s+', ' ')
  $safe = $safe.TrimEnd('.', ' ')
  if (-not $safe) {
    return $Default
  }
  return $safe
}

function Repair-MojibakeText {
  param([string]$Text)

  if (-not $Text) {
    return $Text
  }

  $mojibakeScore = 0
  foreach ($ch in $Text.ToCharArray()) {
    $code = [int][char]$ch
    if (($code -ge 0x00C0 -and $code -le 0x00FF) -or $code -eq 0xFFFD) {
      $mojibakeScore++
    }
  }
  if ($mojibakeScore -lt 3) {
    return $Text
  }

  try {
    $bytes = [Text.Encoding]::GetEncoding("ISO-8859-1").GetBytes($Text)
    $repaired = [Text.Encoding]::UTF8.GetString($bytes)
    $repairedScore = 0
    foreach ($ch in $repaired.ToCharArray()) {
      $code = [int][char]$ch
      if (($code -ge 0x00C0 -and $code -le 0x00FF) -or $code -eq 0xFFFD) {
        $repairedScore++
      }
    }
    if ($repairedScore -lt $mojibakeScore) {
      return $repaired
    }
  } catch {
    return $Text
  }

  return $Text
}

function Normalize-HtmlText {
  param([string]$Html)

  $normalized = Repair-MojibakeText $Html
  if (-not $normalized) {
    return $normalized
  }

  if ($normalized -match '<head[^>]*>' -and $normalized -notmatch '(?i)<meta\s+charset=') {
    $normalized = [regex]::Replace($normalized, '(?i)(<head[^>]*>)', '$1' + [Environment]::NewLine + '<meta charset="UTF-8">', 1)
  }
  return $normalized
}

function Export-CsvUtf8Bom {
  param(
    [Parameter(Mandatory = $true)] [array]$Rows,
    [Parameter(Mandatory = $true)] [string]$Path
  )

  $csv = $Rows | ConvertTo-Csv -NoTypeInformation
  [System.IO.File]::WriteAllLines($Path, $csv, [Text.UTF8Encoding]::new($true))
}

function Get-ModelOutputDirectory {
  param(
    [Parameter(Mandatory = $true)] [string]$BaseOutputDir,
    [string]$Model
  )

  $modelName = $Model
  if (-not $modelName) {
    $modelName = "default"
  }
  $safeModelName = Convert-ToSafeFileName -Value $modelName -Default "default"
  return Join-Path $BaseOutputDir $safeModelName
}

function Invoke-JsonRequest {
  param(
    [Parameter(Mandatory = $true)] [string]$Uri,
    [Parameter(Mandatory = $true)] [string]$Method,
    [object]$Body = $null,
    [hashtable]$Headers = @{}
  )

  $params = @{
    Uri = $Uri
    Method = $Method
    Headers = $Headers
  }

  if ($null -ne $Body) {
    $params.ContentType = "application/json; charset=utf-8"
    $params.Body = ($Body | ConvertTo-Json -Depth 10)
  }

  return Invoke-RestMethod @params
}

function Send-PdfUpload {
  param(
    [Parameter(Mandatory = $true)] [string]$Uri,
    [Parameter(Mandatory = $true)] [string]$Token,
    [Parameter(Mandatory = $true)] [string]$FilePath,
    [Parameter(Mandatory = $true)] [hashtable]$Fields,
    [int]$TimeoutMinutes = 90
  )

  $handler = New-Object System.Net.Http.HttpClientHandler
  $client = New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout = [TimeSpan]::FromMinutes([Math]::Max($TimeoutMinutes, 5))
  $client.DefaultRequestHeaders.Authorization =
    New-Object System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", $Token)

  $content = New-Object System.Net.Http.MultipartFormDataContent
  $fileStream = $null

  try {
    foreach ($key in $Fields.Keys) {
      $value = $Fields[$key]
      if ($null -ne $value -and ([string]$value).Length -gt 0) {
        $stringPart = New-Object System.Net.Http.StringContent(([string]$value), [Text.Encoding]::UTF8)
        $content.Add($stringPart, $key)
      }
    }

    $fileStream = [System.IO.File]::OpenRead($FilePath)
    $filePart = New-Object System.Net.Http.StreamContent($fileStream)
    $filePart.Headers.ContentType =
      [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("application/pdf")
    $content.Add($filePart, "file", [System.IO.Path]::GetFileName($FilePath))

    $response = $client.PostAsync($Uri, $content).GetAwaiter().GetResult()
    $responseText = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

    if (-not $response.IsSuccessStatusCode) {
      throw "HTTP $([int]$response.StatusCode): $responseText"
    }

    return $responseText | ConvertFrom-Json
  } finally {
    if ($null -ne $content) { $content.Dispose() }
    if ($null -ne $fileStream) { $fileStream.Dispose() }
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $handler) { $handler.Dispose() }
  }
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

function Save-Results {
  param(
    [Parameter(Mandatory = $true)] [array]$Rows,
    [Parameter(Mandatory = $true)] [string]$Path
  )

  Export-CsvUtf8Bom -Rows $Rows -Path $Path
}

$BaseUrl = $BaseUrl.TrimEnd("/")
$WebBaseUrl = $BaseUrl -replace "/api$", ""

if (-not (Test-Path -LiteralPath $CsvPath)) {
  throw "CSV file not found: $CsvPath"
}

$csvProbe = [System.IO.File]::OpenRead($CsvPath)
try {
  if ($csvProbe.Length -ge 2) {
    $b0 = $csvProbe.ReadByte()
    $b1 = $csvProbe.ReadByte()
    if ($b0 -eq 0x50 -and $b1 -eq 0x4B) {
      throw "Input file looks like an Excel .xlsx workbook, not a real CSV: $CsvPath. Please save/export it as CSV UTF-8, or use the prepared CSV file directly."
    }
  }
} finally {
  $csvProbe.Dispose()
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
  New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

if (-not $Email) {
  $Email = Read-Host "MAIC-UI account email"
}

if (-not $Password) {
  $securePassword = Read-Host "MAIC-UI password" -AsSecureString
  $Password = Convert-SecureStringToPlainText $securePassword
}

Write-Host ""
Write-Host "Logging in to $BaseUrl ..."
$login = Invoke-JsonRequest `
  -Uri "$BaseUrl/auth/login" `
  -Method "Post" `
  -Body @{ email = $Email; password = $Password }

$token = $login.access_token
if (-not $token) {
  throw "Login succeeded but no access token was returned."
}
Write-Host "Login OK: $($login.user.email)"

$csvFullPath = [System.IO.Path]::GetFullPath($CsvPath)
$csvDirectory = Split-Path -Parent $csvFullPath
$rows = Import-Csv -LiteralPath $csvFullPath -Encoding UTF8
if (-not $rows -or $rows.Count -eq 0) {
  throw "CSV has no rows: $csvFullPath"
}
if ($rows[0].PSObject.Properties.Name -notcontains "file") {
  throw "CSV is missing required column 'file': $csvFullPath"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resultPath = Join-Path $OutputDir "batch-results-$timestamp.csv"
$sliceDir = Join-Path $OutputDir "pdf-page-slices-$timestamp"
$results = @()

Write-Host ""
Write-Host "Loaded $($rows.Count) course row(s). Results: $resultPath"
Write-Host "Generation is sequential for stability."
Write-Host ""

$previousCourses = @()

$index = 0
foreach ($row in $rows) {
  $index += 1
  $rawFile = Get-Cell $row "file"
  $title = Get-Cell $row "title"

  if (-not $rawFile) {
    Write-Warning "Row $index skipped: missing file"
    continue
  }

  $filePath = Resolve-InputFile -PathFromCsv $rawFile -CsvDirectory $csvDirectory
  if (-not (Test-Path -LiteralPath $filePath)) {
    Write-Warning "Row $index failed: file not found: $filePath"
    $results += [PSCustomObject]@{
      row = $index
      title = $title
      file = $rawFile
      document_id = ""
      status = "file_not_found"
      document_url = ""
      public_view_url = ""
      html_path = ""
      error = "File not found: $filePath"
    }
    Save-Results -Rows $results -Path $resultPath
    continue
  }

  if (-not $title) {
    $title = [System.IO.Path]::GetFileNameWithoutExtension($filePath)
  }

  $subject = Get-Cell $row "subject"
  $gradeLevel = Get-Cell $row "grade_level"
  $description = Get-Cell $row "description"
  $pageRange = Get-Cell $row "page_range"
  $generationMode = Get-Cell $row "generation_mode" "fast"
  $aiModel = Get-Cell $row "ai_model"
  if ($aiModel.ToLowerInvariant() -eq "default") {
    $aiModel = ""
  }
  $isPublic = Convert-CellToBoolString (Get-Cell $row "is_public" "false")

  $conceptData = @{}
  foreach ($fieldName in @("lesson_id", "source_chapter", "source_section", "page_range", "concept_name", "concept_overview", "mastery_points", "design_idea", "assessment_focus", "tracking_plan")) {
    $value = Get-Cell $row $fieldName
    if ($value) {
      $conceptData[$fieldName] = $value
    }
  }

  $prefs = @{}
  if ($conceptData.Count -gt 0) {
    $prefs["concept_data"] = $conceptData
    $prefs["pdf_generation_mode"] = "pdf_with_concept_focus"
  }

  $includePrerequisites = Get-Cell $row "include_prerequisites"
  if ($includePrerequisites) {
    $prefs["include_prerequisites"] = ((Convert-CellToBoolString $includePrerequisites "true") -eq "true")
  }

  $includeExercises = Get-Cell $row "include_exercises"
  if ($includeExercises) {
    $prefs["include_exercises"] = ((Convert-CellToBoolString $includeExercises "true") -eq "true")
  }

  $fields = @{
    title = $title
    is_public = $isPublic
    generation_mode = $generationMode
    user_preferences = ($prefs | ConvertTo-Json -Depth 10 -Compress)
  }
  if ($subject) { $fields['subject'] = $subject }
  if ($gradeLevel) { $fields['grade_level'] = $gradeLevel }
  if ($description) { $fields['description'] = $description }
  if ($pageRange) {
    $descText = ""
    if ($fields.ContainsKey('description')) {
      $descText = [string]$fields.Get_Item('description')
    }
    $rangeText = 'Original PDF page range: ' + $pageRange + '. Use only this sliced PDF section and the concept focus above. Do not infer from the whole chapter.'
    $descText = ($descText + [Environment]::NewLine + $rangeText).Trim()
    $fields.Set_Item('description', $descText)
  }
  if ($previousCourses.Count -gt 0) {
    $prevSummary = "Previous courses in this batch (avoid repeating these concepts; you may briefly recall them in the entry diagnostic):`n"
    for ($pi = 0; $pi -lt $previousCourses.Count; $pi++) {
      $prevSummary += ($pi + 1).ToString() + ". " + $previousCourses[$pi] + "`n"
    }
    $descText = [string]$fields.Get_Item('description')
    $descText = ($descText + [Environment]::NewLine + $prevSummary.TrimEnd()).Trim()
    $fields.Set_Item('description', $descText)
  }
  if ($aiModel) { $fields['ai_model'] = $aiModel }

  $documentId = ""
  $status = "uploading"
  $errorText = ""
  $htmlPath = ""
  $documentUrl = ""
  $publicViewUrl = ""

  try {
    $uploadFilePath = $filePath
    if ($pageRange) {
      $uploadFilePath = New-PdfPageSlice `
        -SourcePath $filePath `
        -PageRange $pageRange `
        -OutputDir $sliceDir `
        -RowIndex $index
      Write-Host "  Page slice: $pageRange -> $uploadFilePath"
    }

    Write-Host "[$index/$($rows.Count)] Uploading: $title"
    $upload = Send-PdfUpload `
      -Uri "$BaseUrl/pdf/upload" `
      -Token $token `
      -FilePath $uploadFilePath `
      -Fields $fields `
      -TimeoutMinutes $TimeoutMinutes

    $documentId = $upload.id
    $documentUrl = "$WebBaseUrl/document/$documentId"
    if ($isPublic -eq "true") {
      $publicViewUrl = "$WebBaseUrl/public/document/view/$documentId"
    }

    Write-Host "  Document ID: $documentId"

    if ($NoWait) {
      $status = "submitted"
    } else {
      $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
      do {
        Start-Sleep -Seconds $PollSeconds
        $statusResponse = Invoke-JsonRequest `
          -Uri "$BaseUrl/pdf/documents/$documentId/processing-status" `
          -Method "Get" `
          -Headers @{ Authorization = "Bearer $token" }

        $status = $statusResponse.status
        Write-Host "  Status: $status ($($statusResponse.progress)%)"

        if ($status -eq "ready" -or $status -eq "error") {
          break
        }
      } while ((Get-Date) -lt $deadline)

      if ($status -ne "ready" -and $status -ne "error") {
        $status = "timeout"
        $errorText = "Timed out after $TimeoutMinutes minutes"
      }

      if ($status -eq "error") {
        $doc = Invoke-JsonRequest `
          -Uri "$BaseUrl/pdf/documents/$documentId" `
          -Method "Get" `
          -Headers @{ Authorization = "Bearer $token" }
        $errorText = $doc.error_message
      }

      if ($status -eq "ready" -and $DownloadHtml) {
        $website = Invoke-JsonRequest `
          -Uri "$BaseUrl/pdf/documents/$documentId/website" `
          -Method "Get" `
          -Headers @{ Authorization = "Bearer $token" }

        $modelOutputDir = Get-ModelOutputDirectory -BaseOutputDir $OutputDir -Model $aiModel
        if (-not (Test-Path -LiteralPath $modelOutputDir)) {
          New-Item -ItemType Directory -Path $modelOutputDir -Force | Out-Null
        }

        $safeName = Convert-ToSafeFileName -Value $title -Default "document-$documentId"
        $htmlPath = Join-Path $modelOutputDir "$safeName.html"
        $html = Normalize-HtmlText ([string]$website.html)
        [System.IO.File]::WriteAllText($htmlPath, $html, [Text.UTF8Encoding]::new($true))
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
      document_id = $documentId
      status = $status
    document_url = $documentUrl
    public_view_url = $publicViewUrl
    html_path = $htmlPath
    error = $errorText
  }
  $curConcept = Get-Cell $row "concept_name"
  $curLessonId = Get-Cell $row "lesson_id"
  if ($curConcept) {
    $prevEntry = $curLessonId + " " + $title + " - " + $curConcept
    $previousCourses += $prevEntry
  } elseif ($title) {
    $previousCourses += $title
  }
  Save-Results -Rows $results -Path $resultPath
  Write-Host ""
}

Write-Host "Done."
Write-Host "Results saved to: $resultPath"
