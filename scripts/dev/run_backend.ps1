$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Missing .venv Python at $python. Create the venv first."
}

if (Test-Path ".env") {
  Write-Host "Loading .env for DB / cloud config..."
  Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $name = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim()
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}

$env:OBITAL_LOCAL_SINGLE_USER = "1"

if ($env:DATABASE_URL) {
  Write-Host "DATABASE_URL detected, running migrations..."
  if (-not $env:DATABASE_MIGRATION_URL) {
    Write-Host "DATABASE_MIGRATION_URL not set, using DATABASE_URL for migrations."
  }
  & $python -m alembic -c backend\alembic.ini upgrade head
  if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed."
  }
}

& $python -m uvicorn backend.app.main:app --reload --port 8000
