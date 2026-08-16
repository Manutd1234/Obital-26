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

if ($env:LOCAL_DATABASE_URL) {
  Write-Host "Clearing LOCAL_DATABASE_URL for cloud/Postgres primary mode."
  [Environment]::SetEnvironmentVariable("LOCAL_DATABASE_URL", "", "Process")
}
$env:OBITAL_FORCE_CLOUD_DB = "1"

if (-not $env:DATABASE_MIGRATION_URL) {
  Write-Host "DATABASE_MIGRATION_URL not set; using DATABASE_URL for migrations."
}

Write-Host "Running Alembic migrations..."
& $python -m alembic -c backend\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
  throw "Alembic migration failed."
}

& $python -m uvicorn backend.app.main:app --reload --port 8000
