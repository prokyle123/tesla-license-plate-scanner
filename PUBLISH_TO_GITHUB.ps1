<#
Creates and pushes this clean repository using the GitHub CLI.
Default visibility is private. Add -Public only when you are ready to make it public.

Examples:
  Set-ExecutionPolicy -Scope Process Bypass -Force
  .\PUBLISH_TO_GITHUB.ps1
  .\PUBLISH_TO_GITHUB.ps1 -Public
#>
[CmdletBinding()]
param(
    [string]$Name = "tesla-license-plate-scanner",
    [switch]$Public
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI ('gh') is not installed. Install it with: winget install --id GitHub.cli"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or is not on PATH."
}

Write-Host "Checking GitHub authentication..." -ForegroundColor Cyan
gh auth status
$Owner = (gh api user --jq '.login').Trim()
if (-not $Owner) { throw "Could not determine the authenticated GitHub account." }
$FullName = "$Owner/$Name"

# A first-time Git install may not have an identity yet. Set it locally for
# this repository only so the initial commit does not fail.
if (-not (git config user.name)) {
    git config user.name $Owner
}
if (-not (git config user.email)) {
    git config user.email "$Owner@users.noreply.github.com"
}

if (-not (Test-Path (Join-Path $ProjectRoot '.git'))) {
    git init | Out-Host
}
git branch -M main
git add -A

$HasStagedChanges = git diff --cached --quiet; $LASTEXITCODE -ne 0
if ($HasStagedChanges) {
    git commit -m "Initial public-ready release" | Out-Host
}

$Existing = $false
try {
    gh repo view $FullName *> $null
    $Existing = $true
} catch { $Existing = $false }

if ($Existing) {
    throw "Repository $FullName already exists. Pick another name with -Name, or add this folder as a remote manually."
}

$Visibility = if ($Public) { "--public" } else { "--private" }
Write-Host "Creating $FullName..." -ForegroundColor Cyan
gh repo create $FullName $Visibility --source . --remote origin --push `
    --description "Tesla license plate scanner using built-in Tesla cameras, CodeProject.AI, OCR, and a local Flask dashboard." | Out-Host

# These are optional metadata only; a failure here does not affect the repository or push.
try {
    gh repo edit $FullName --add-topic tesla --add-topic teslacam --add-topic alpr --add-topic flask --add-topic codeproject-ai --add-topic raspberry-pi | Out-Host
} catch {
    Write-Warning "Repository created and pushed, but GitHub topics were not set: $($_.Exception.Message)"
}

Write-Host "Done: https://github.com/$FullName" -ForegroundColor Green

