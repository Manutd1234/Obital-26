$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Missing .venv Python at $python. Create the venv first."
}

& $python -m pip install -r frontend\requirements.txt
& $python -m frontend.app $args
