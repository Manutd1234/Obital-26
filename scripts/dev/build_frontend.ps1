# Build React frontend and copy to backend templates for Vercel deployment.
#
# Usage:
#   .\scripts\dev\build_frontend.ps1
#
# Automatically commits and pushes the built UI to git.

$ErrorActionPreference = "Stop"

# Resolve paths from the repository root so this command is safe to launch
# from PowerShell, an IDE terminal, or a CI checkout.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

Write-Host "Installing frontend dependencies..."
Push-Location frontend
npm install
if ($LASTEXITCODE -ne 0) { throw "npm install failed." }

Write-Host "Building React frontend..."
npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed." }
Pop-Location

$distPath = "frontend\dist"
$templateDir = "backend\app\templates"

if (-not (Test-Path $distPath)) {
    throw "Build output not found at $distPath"
}

Write-Host "Copying build output to $templateDir..."
if (-not (Test-Path $templateDir)) {
    New-Item -ItemType Directory -Path $templateDir | Out-Null
}

# Remove the previous build in full, including subdirectories.
#
# This used to be `Get-ChildItem $templateDir -File`, which without -Recurse
# matched only top-level files and never entered assets/. Because Copy-Item
# below merges rather than replaces, every build left its content-hashed
# bundle behind: ~30 builds accumulated 60 orphaned files (~45 MB) that
# nothing references. Deleting the directory outright keeps assets/ holding
# exactly one build.
Get-ChildItem $templateDir -Force | Remove-Item -Recurse -Force

# Copy new build
Copy-Item "$distPath\*" -Destination $templateDir -Recurse -Force

Write-Host "Done. Built React UI copied to $templateDir"

Write-Host "Staging and committing built frontend..."
git add backend/app/templates/
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
git commit -m "build: update frontend UI ($timestamp)"
if ($LASTEXITCODE -ne 0) { throw "git commit failed." }

Write-Host "Pushing to remote..."
git push
if ($LASTEXITCODE -ne 0) { throw "git push failed." }

Write-Host "Frontend built, committed, and pushed."
