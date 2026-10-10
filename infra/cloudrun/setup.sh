#!/usr/bin/env bash
# ONE-TIME Google Cloud setup: APIs, service accounts + IAM, Artifact Registry, private bucket, Cloud SQL, secrets.
# Nothing here deploys the application. Review every command, then run:   source env.local.sh && ./setup.sh
set -euo pipefail
: "${PROJECT_ID:?source env.local.sh first}"
gcloud config set project "$PROJECT_ID"

echo "== 1. APIs =="
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  aiplatform.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  storage.googleapis.com iam.googleapis.com iamcredentials.googleapis.com \
  logging.googleapis.com monitoring.googleapis.com
# Only if you choose private-IP Cloud SQL + Direct VPC egress:  gcloud services enable servicenetworking.googleapis.com compute.googleapis.com

echo "== 2. Service accounts (one per service, least privilege) =="
for sa in interview-backend interview-notify interview-frontend; do
  gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$sa" --display-name "$sa"
done

echo "== 3. Artifact Registry =="
gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$AR_REPO" --repository-format docker --location "$REGION" \
       --description "Interview platform images"

echo "== 4. Private Cloud Storage bucket (resumes + recordings) =="
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
       --uniform-bucket-level-access --public-access-prevention
# Retention: delete objects after N days (match DATA_RETENTION_DAYS in the consent form).
cat > /tmp/lifecycle.json <<JSON
{"rule":[{"action":{"type":"Delete"},"condition":{"age":90}}]}
JSON
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=/tmp/lifecycle.json

echo "== 5. IAM =="
# Backend: Vertex AI (Gemini), Cloud SQL, the bucket, logging/metrics.
for role in roles/aiplatform.user roles/cloudsql.client roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$BACKEND_SA" --role "$role" --condition=None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$BACKEND_SA" --role roles/storage.objectAdmin >/dev/null
# Signed URLs without a key file: the backend service account signs as itself through the IAM Credentials API.
gcloud iam service-accounts add-iam-policy-binding "$BACKEND_SA" \
  --member "serviceAccount:$BACKEND_SA" --role roles/iam.serviceAccountTokenCreator >/dev/null
for sa in "$NOTIFY_SA" "$FRONTEND_SA"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$sa" --role roles/logging.logWriter --condition=None >/dev/null
done

echo "== 6. Cloud SQL for PostgreSQL =="
gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1 \
  || gcloud sql instances create "$SQL_INSTANCE" --database-version POSTGRES_16 --region "$REGION" \
       --edition ENTERPRISE --tier db-custom-1-3840 --storage-type SSD --storage-size 10 --storage-auto-increase \
       --availability-type zonal --backup-start-time 02:00 --enable-point-in-time-recovery \
       --deletion-protection
gcloud sql databases describe "$SQL_DB" --instance "$SQL_INSTANCE" >/dev/null 2>&1 \
  || gcloud sql databases create "$SQL_DB" --instance "$SQL_INSTANCE"

echo "== 7. Secrets (Secret Manager) =="
mk_secret() {  # name, value-from-stdin
  if gcloud secrets describe "$1" >/dev/null 2>&1; then gcloud secrets versions add "$1" --data-file=-
  else gcloud secrets create "$1" --replication-policy automatic --data-file=-; fi
}
DB_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
gcloud sql users describe "$SQL_USER" --instance "$SQL_INSTANCE" >/dev/null 2>&1 \
  && gcloud sql users set-password "$SQL_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD" \
  || gcloud sql users create "$SQL_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD"
CONN="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
printf 'postgresql+psycopg2://%s:%s@/%s?host=/cloudsql/%s' "$SQL_USER" "$DB_PASSWORD" "$SQL_DB" "$CONN" | mk_secret database-url
python3 -c 'import secrets;print(secrets.token_urlsafe(48),end="")' | mk_secret jwt-secret
python3 -c 'import secrets;print(secrets.token_urlsafe(32),end="")' | mk_secret notification-token
unset DB_PASSWORD
# Provider credentials: create them when you pick a provider (values are read from stdin, never from a file in Git):
#   printf '%s' "$SENDGRID_API_KEY"   | gcloud secrets create sendgrid-api-key   --data-file=-
#   printf '%s' "$SMTP_PASSWORD"      | gcloud secrets create smtp-password      --data-file=-
#   printf '%s' "$TWILIO_AUTH_TOKEN"  | gcloud secrets create twilio-auth-token  --data-file=-
for s in database-url jwt-secret notification-token; do
  gcloud secrets add-iam-policy-binding "$s" --member "serviceAccount:$BACKEND_SA" --role roles/secretmanager.secretAccessor >/dev/null
done
gcloud secrets add-iam-policy-binding notification-token --member "serviceAccount:$NOTIFY_SA" \
  --role roles/secretmanager.secretAccessor >/dev/null

echo "Done. Next: ./deploy.sh"
