param(
  [string]$CsvPath = (Join-Path $PSScriptRoot "openmaic-courses.csv"),
  [string]$BaseUrl = "http://localhost:3001",
  [string]$AccessCode = "",
  [int]$TextLimit = 50000,
  [int]$ImageLimit = 20
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function New-AccessCookieHeader {
  param(
    [Parameter(Mandatory = $true)] [string]$BaseUrl,
    [Parameter(Mandatory = $true)] [string]$AccessCode
  )

  if (-not $AccessCode) { return $null }
  $body = @{ code = $AccessCode } | ConvertTo-Json -Compress
  $response = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "$($BaseUrl.TrimEnd('/'))/api/access-code/verify" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body $body
  $setCookie = [string]$response.Headers["Set-Cookie"]
  if (-not $setCookie) { return $null }
  return ($setCookie -split ";")[0]
}

function Send-ParsePdf {
  param(
    [Parameter(Mandatory = $true)] [string]$Uri,
    [Parameter(Mandatory = $true)] [string]$FilePath,
    [string]$CookieHeader = ""
  )
  $curlArgs = @(
    "-sS",
    "--fail-with-body",
    "-X", "POST",
    "-H", "Cookie: $CookieHeader",
    "-F", "providerId=unpdf",
    "-F", "pdf=@$FilePath;type=application/pdf",
    $Uri
  )
  $text = & curl.exe @curlArgs
  if ($LASTEXITCODE -ne 0) {
    throw "curl failed with exit code ${LASTEXITCODE}: $text"
  }
  return ($text -join "`n") | ConvertFrom-Json
}

$rows = Import-Csv -LiteralPath $CsvPath -Encoding UTF8
$cookieHeader = New-AccessCookieHeader -BaseUrl $BaseUrl -AccessCode $AccessCode
$results = @()
foreach ($row in $rows) {
  Write-Host "Parsing: $($row.title)"
  $parsed = Send-ParsePdf -Uri "$($BaseUrl.TrimEnd('/'))/api/parse-pdf" -FilePath $row.file -CookieHeader $cookieHeader
  $data = $parsed.data
  $textLength = ([string]$data.text).Length
  $imageCount = @($data.images).Count
  $pageCount = [int]$data.metadata.pageCount
  $results += [PSCustomObject]@{
    title = $row.title
    file = $row.file
    page_count = $pageCount
    text_chars = $textLength
    image_count = $imageCount
    text_ok = $textLength -le $TextLimit
    images_ok = $imageCount -le $ImageLimit
  }
}

$results | Format-Table -AutoSize

$out = Join-Path $PSScriptRoot "outputs\openmaic-parse-limits.csv"
New-Item -ItemType Directory -Path (Split-Path -Parent $out) -Force | Out-Null
$results | Export-Csv -LiteralPath $out -NoTypeInformation -Encoding UTF8
Write-Host "Saved: $out"
