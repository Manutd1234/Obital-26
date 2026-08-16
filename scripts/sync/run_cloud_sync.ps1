# Admin/recovery script for cloud sync (local -> cloud).
# For user-facing sync, use F7 in the Textual frontend which calls POST /sync/push via the unlock token model.
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

if (-not $env:DATABASE_MIGRATION_URL) {
  Write-Host "DATABASE_MIGRATION_URL not set; using DATABASE_URL for migrations."
}

if (-not $Passphrase) {
  if ($env:ACCOUNT_SYNC_PASSPHRASE) {
    $Passphrase = $env:ACCOUNT_SYNC_PASSPHRASE
  } else {
    throw "Missing sync passphrase. Pass -Passphrase or set ACCOUNT_SYNC_PASSPHRASE."
  }
}

Write-Host "Syncing account '$AccountId'..."
Write-Host "Running Alembic migrations..."
& $python -m alembic -c backend\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
  throw "Alembic migration failed."
}

Write-Host "Importing local account data into cloud database..."
& $python -m backend.scripts.migrate_file_data_to_db --account-id $AccountId --passphrase $Passphrase
if ($LASTEXITCODE -ne 0) {
  throw "Data import failed."
}

Write-Host "Cloud sync complete."
