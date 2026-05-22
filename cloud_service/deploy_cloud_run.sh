#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${WEBHOOK_TOKEN:?Set WEBHOOK_TOKEN}"

REGION="${REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-competitors-parser-webhook}"
JOB_NAME="${JOB_NAME:-competitors-parser-job}"
SCHEDULER_NAME="${SCHEDULER_NAME:-competitors-parser-0700-msk}"
REPOSITORY="${REPOSITORY:-parser}"
SPREADSHEET_ID="${SPREADSHEET_ID:-151fl2XsI_gmqPXIhFA47OZ-nSQEMBbDb2JaKNXN9be0}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/competitors-parser:latest"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

gcloud config set project "${PROJECT_ID}"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com sheets.googleapis.com cloudscheduler.googleapis.com

gcloud artifacts repositories create "${REPOSITORY}" \
  --repository-format=docker \
  --location="${REGION}" \
  --quiet || true

gcloud builds submit --tag "${IMAGE}" .

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/run.developer" \
  --quiet

gcloud run jobs deploy "${JOB_NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --service-account="${SERVICE_ACCOUNT}" \
  --command=python \
  --args=-m,cloud_service.run_update \
  --set-env-vars="SPREADSHEET_ID=${SPREADSHEET_ID}" \
  --cpu=2 \
  --memory=2Gi \
  --task-timeout=7200 \
  --max-retries=0

gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --service-account="${SERVICE_ACCOUNT}" \
  --set-env-vars="SPREADSHEET_ID=${SPREADSHEET_ID},WEBHOOK_TOKEN=${WEBHOOK_TOKEN},CLOUD_RUN_JOB_NAME=${JOB_NAME},CLOUD_RUN_REGION=${REGION},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --cpu=1 \
  --memory=512Mi \
  --timeout=300

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" \
  --format='value(status.url)')"

if gcloud scheduler jobs describe "${SCHEDULER_NAME}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_NAME}" \
    --location="${REGION}" \
    --schedule="0 7 * * *" \
    --time-zone="Europe/Moscow" \
    --uri="${SERVICE_URL}/run" \
    --http-method=POST \
    --headers="Authorization=Bearer ${WEBHOOK_TOKEN},Content-Type=application/json" \
    --message-body='{"source":"cloud_scheduler_07_00"}'
else
  gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
    --location="${REGION}" \
    --schedule="0 7 * * *" \
    --time-zone="Europe/Moscow" \
    --uri="${SERVICE_URL}/run" \
    --http-method=POST \
    --headers="Authorization=Bearer ${WEBHOOK_TOKEN},Content-Type=application/json" \
    --message-body='{"source":"cloud_scheduler_07_00"}'
fi

cat <<EOF
Cloud Run URL: ${SERVICE_URL}
Apps Script PARSER_WEBHOOK_URL: ${SERVICE_URL}/run
Apps Script PARSER_WEBHOOK_TOKEN: ${WEBHOOK_TOKEN}

Share the Google Sheet with editor access to:
${SERVICE_ACCOUNT}
EOF
