# push.ps1 — Push to GitHub using PAT from local file.
# Run: .\push.ps1
# Delete Pat_Key.md after Oct 1 when token expires.

$patFile = "C:\Users\AbadUmairChanna\OneDrive - Verge Mobile\Desktop\Pat_Key.md"

if (-not (Test-Path $patFile)) {
    Write-Error "PAT file not found: $patFile"
    exit 1
}

$pat = (Get-Content $patFile -Raw).Trim()

if (-not $pat) {
    Write-Error "PAT file is empty."
    exit 1
}

$remote = "https://${pat}@github.com/abaduchanna/GFH-Audit-Automation.git"

Write-Host "Pushing to origin/main..." -ForegroundColor Cyan
git push $remote main

if ($LASTEXITCODE -eq 0) {
    Write-Host "Push successful." -ForegroundColor Green
} else {
    Write-Host "Push failed. Check PAT permissions and expiry." -ForegroundColor Red
}
