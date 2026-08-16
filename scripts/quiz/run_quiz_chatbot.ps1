$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Missing .venv Python at $python. Create the venv first."
}

if (-not (Test-Path "data\models\sentence_transformers\models--sentence-transformers--all-MiniLM-L6-v2")) {
  & $python -m engine.quiz.model_setup
}
& $python -m engine.quiz.quiz_chatbot_cli
