$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Missing .venv Python at $python. Create the venv first."
}

# Load .env into process so DATABASE_URL is available (cloud mode).
# If .env is missing or DATABASE_URL is not set, backend falls back to local mode.
if (Test-Path ".env") {
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

# Local single-user mode: bypass token authentication.
# Only set when DATABASE_URL is absent (local development). In cloud mode,
# token authentication is enforced automatically.
if (-not $env:DATABASE_URL) {
  $env:OBITAL_LOCAL_SINGLE_USER = "1"
}

# Ensure no stale backend process is holding port 8000.
try {
  $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop
  foreach ($c in $conn) {
    if ($c.OwningProcess -and $c.OwningProcess -ne 0) {
      Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
  }
} catch {
  # No listener found on 8000; continue.
}

# Ensure frontend dependencies are present.
& $python -m pip install -r frontend\requirements.txt | Out-Host

# Keep the cloud schema current before serving API requests that depend on
# unlock/session/sync tables.
if ($env:DATABASE_URL) {
  if (-not $env:DATABASE_MIGRATION_URL) {
    Write-Host "DATABASE_MIGRATION_URL not set; using DATABASE_URL for migrations."
  }
  Write-Host "Running Alembic migrations..."
  & $python -m alembic -c backend\alembic.ini upgrade head | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed."
  }
}

# Start backend in background.
$backendArgs = @(
  "-m",
  "uvicorn",
  "backend.app.main:app",
  "--host",
  "127.0.0.1",
  "--port",
  "8000"
)
$backendProc = Start-Process -FilePath $python -ArgumentList $backendArgs -PassThru -WindowStyle Hidden

try {
  $ready = $false
  for ($i = 0; $i -lt 30; $i++) {
    try {
      $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 1
      if ($resp.StatusCode -eq 200) {
        $ready = $true
        break
      }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $ready) {
    throw "Backend did not become ready on http://127.0.0.1:8000."
  }
  Write-Host "Backend ready. Web UI: http://127.0.0.1:8000/"
  & $python -m frontend.app
}
finally {
  if ($backendProc -and -not $backendProc.HasExited) {
    Stop-Process -Id $backendProc.Id -Force
  }
}
