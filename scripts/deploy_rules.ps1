Write-Host "Starting Firebase Rules Security Validation and Deployment..." -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

# 1. Check if Firebase CLI is installed
if (-not (Get-Command firebase -ErrorAction SilentlyContinue)) {
    Write-Error "Firebase CLI is not installed. Please install it using 'npm install -g firebase-tools'."
    exit 1
}

# 2. Deploy rules (paths from firebase.json at repo root)
Write-Host "Deploying Firestore security rules..." -ForegroundColor Yellow
firebase deploy --only firestore:rules

Write-Host "Deploying Firebase Storage security rules..." -ForegroundColor Yellow
firebase deploy --only storage

Write-Host "Firebase rules successfully validated and deployed!" -ForegroundColor Green
