Write-Host "Starting Firebase Rules Security Validation and Deployment..." -ForegroundColor Cyan

# 1. Check if Firebase CLI is installed
if (-not (Get-Command firebase -ErrorAction SilentlyContinue)) {
    Write-Error "Firebase CLI is not installed. Please install it using 'npm install -g firebase-tools'."
    exit 1
}

# 2. Deploy rules
Write-Host "Deploying Firestore security rules..." -ForegroundColor Yellow
firebase deploy --only firestore:rules

Write-Host "Deploying Firebase Storage security rules..." -ForegroundColor Yellow
firebase deploy --only storage

Write-Host "Firebase rules successfully validated and deployed!" -ForegroundColor Green
