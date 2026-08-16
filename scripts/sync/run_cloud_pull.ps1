# Admin/recovery script for cloud pull (cloud -> local).
# For user-facing sync, use F8 in the Textual frontend which calls POST /sync/pull via the unlock token model.
param(
  [Parameter(Mandatory = $true)]
  [string]$AccountId,
  [string]$Passphrase = ""
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Missing .venv Python at $python. Create the venv first."
}

if (-not (Test-Path ".env")) {
  throw "Missing .env at repo root."
}

Get-Content ".env" | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $idx = $line.IndexOf("=")
  if ($idx -lt 1) { return }
  $name = $line.Substring(0, $idx).Trim()
  $value = $line.Substring($idx + 1).Trim()
  [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is not set in .env."
}

if (-not $Passphrase) {
  if ($env:ACCOUNT_SYNC_PASSPHRASE) {
    $Passphrase = $env:ACCOUNT_SYNC_PASSPHRASE
  } else {
    throw "Missing sync passphrase. Pass -Passphrase or set ACCOUNT_SYNC_PASSPHRASE."
  }
}

Write-Host "Pulling account '$AccountId' from cloud DB..."
Write-Host "Exporting cloud DB data to local backend/data files..."
& $python -m backend.scripts.export_db_to_file_data --account-id $AccountId --passphrase $Passphrase
if ($LASTEXITCODE -ne 0) {
  throw "Cloud pull/export failed."
}

Write-Host "Cloud pull complete."
