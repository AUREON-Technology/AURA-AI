# =========================================================================
# AURA AI INFRASTRUCTURE INITIALIZATION SCRIPT
# =========================================================================

# Add CLI paths to environment Path temporarily
$env:PATH += ";C:\Users\HP\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin;C:\Users\HP\AppData\Roaming\npm"

# 0. Define Configuration Variables
$PROJECT_ID = "aura-ai-anas-gnedy"  # Unique project ID
$REGION = "europe-west3"            # Low latency region close to Egypt (Frankfurt)

Write-Host "Starting AURA AI automation pipeline for Project: $PROJECT_ID..." -ForegroundColor Cyan

# 1. Log in to your cloud accounts
Write-Host "Authenticating tools..." -ForegroundColor Yellow
# gcloud auth login --account=anas.ibrahim.gnedy@gmail.com
# firebase login

# 2. Tasks 1 & 3: Create Google Cloud / Firebase Project
Write-Host "Creating unified GCP/Firebase Project..." -ForegroundColor Yellow
gcloud projects create $PROJECT_ID --name="AURA AI"

# Set gcloud context to our active project
gcloud config set project $PROJECT_ID

# 3. Task 10: Link Billing Account (Required for Cloud Functions/Storage)
# This lists your active billing accounts. Take note of your ACCOUNT_ID if prompted.
Write-Host "Listing available billing accounts to link..." -ForegroundColor Yellow
gcloud billing accounts list
# Manually run the line below with your Account ID if it doesn't automatically bind:
# gcloud billing projects link $PROJECT_ID --billing-account="XXXXXX-XXXXXX-XXXXXX"

# 4. Task 4: Bulk-Enable Required APIs
Write-Host "Enabling Core Services and APIs..." -ForegroundColor Yellow
$APIs = @(
    "firestore.googleapis.com",
    "storage-component.googleapis.com",
    "cloudfunctions.googleapis.com",
    "iam.googleapis.com",
    "calendar-json.googleapis.com",
    "gmail.googleapis.com",
    "tasks.googleapis.com"
)
foreach ($API in $APIs) {
    Write-Host "   -> Activating $API..." -ForegroundColor Cyan
    gcloud services enable $API
}

# 5. Task 6: Provision Firestore Mirror Database
Write-Host "Initializing Cloud Firestore in $REGION..." -ForegroundColor Yellow
gcloud firestore databases create --location=$REGION --type=firestore-native

# 6. Task 7: Setup Cloud Storage Bucket
Write-Host "Creating Cloud Storage Bucket..." -ForegroundColor Yellow
gcloud storage buckets create gs://$PROJECT_ID-bucket --location=$REGION

# 7. Task 11: Set Up Backend IAM Service Account and Generate JSON Key
Write-Host "Generating secure IAM credentials for FastAPI backend..." -ForegroundColor Yellow
$SA_NAME = "aura-backend-server"

# Create Service Account
gcloud iam service-accounts create $SA_NAME --display-name="AURA FastAPI Backend"

# Bind necessary Roles to the Service Account
$ROLES = @("roles/datastore.user", "roles/storage.objectAdmin", "roles/firebase.admin")
foreach ($ROLE in $ROLES) {
    gcloud projects add-iam-policy-binding $PROJECT_ID `
        --member="serviceAccount:$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com" `
        --role=$ROLE
}

# Export the key straight to your computer as credentials.json
gcloud iam service-accounts keys create ./credentials.json `
    --iam-account="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

# 8. Task 9: Configure Budget Alert Limits
Write-Host "Infrastructure Ready. Make sure to set a $5 alert via the UI link." -ForegroundColor Green
Write-Host "Your FastAPI credential file has been saved to: $(Get-Location)\credentials.json" -ForegroundColor Green
