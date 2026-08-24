#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to an existing Google Cloud project}"
: "${LALAL_SECRET_NAME:=lalal-api-key}"

REGION="${REGION:-us-west1}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-nam5}"
BUCKET="${BUCKET:-${PROJECT_ID}-saxscribe}"
REPOSITORY="${REPOSITORY:-saxscribe}"
SERVICE="${SERVICE:-saxscribe-web}"
JOB="${JOB:-saxscribe-worker}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-saxscribe-runtime}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/saxscribe:latest"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com storage.googleapis.com secretmanager.googleapis.com

gcloud artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPOSITORY" --repository-format docker --location "$REGION"

gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${BUCKET}" --location "$REGION" --uniform-bucket-level-access

gcloud iam service-accounts describe "$SERVICE_ACCOUNT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" --display-name "SaxScribe runtime"

for role in roles/storage.objectAdmin roles/datastore.user roles/run.jobsExecutor roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SERVICE_ACCOUNT}" --role "$role" >/dev/null
done

gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 \
  || gcloud firestore databases create --database='(default)' --location "$FIRESTORE_LOCATION" --type=firestore-native

if ! gcloud secrets describe "$LALAL_SECRET_NAME" >/dev/null 2>&1; then
  echo "Create Secret Manager secret '$LALAL_SECRET_NAME' with your LALAL.AI business API key, then rerun this script."
  exit 1
fi

gcloud builds submit --tag "$IMAGE" .

COMMON_ENV="RUNTIME_MODE=gcp,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},GCP_BUCKET=${BUCKET},GCP_JOB_NAME=${JOB},SEPARATION_PROVIDER=lalal,SEPARATION_PRIMARY=lalal,SAX_DEVICE=cpu,MAX_UPLOAD_MB=30,KEEP_JOBS_HOURS=24"

gcloud run jobs deploy "$JOB" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SERVICE_ACCOUNT" \
  --command python \
  --args=-m,backend.cloud_worker \
  --cpu 4 \
  --memory 8Gi \
  --task-timeout 3600s \
  --max-retries 1 \
  --set-env-vars "$COMMON_ENV" \
  --set-secrets "LALAL_API_KEY=${LALAL_SECRET_NAME}:latest"

gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SERVICE_ACCOUNT" \
  --cpu 1 \
  --memory 1Gi \
  --timeout 300 \
  --allow-unauthenticated \
  --set-env-vars "$COMMON_ENV" \
  --set-secrets "LALAL_API_KEY=${LALAL_SECRET_NAME}:latest"

gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
