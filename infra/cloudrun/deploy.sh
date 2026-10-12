#!/usr/bin/env bash
# Build images, run migrations, deploy the three Cloud Run services.   source env.local.sh && ./deploy.sh
# This script DEPLOYS. Do not run it until you are ready (nothing in this repository runs it automatically).
set -euo pipefail
: "${PROJECT_ID:?source env.local.sh first}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONN="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
gcloud config set project "$PROJECT_ID"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

if [ "${SKIP_BUILD:-0}" = "1" ]; then
  echo "== Skipping image builds (SKIP_BUILD=1, using tag $TAG) =="
else
  echo "== Build + push images (Cloud Build; no local Docker needed) =="
  gcloud builds submit "$ROOT/backend"              --tag "$REGISTRY/backend:$TAG"      --timeout 3000s
  gcloud builds submit "$ROOT/notification-service" --tag "$REGISTRY/notification:$TAG"
  gcloud builds submit "$ROOT/frontend"             --tag "$REGISTRY/frontend:$TAG"
fi

echo "== Notification service (private: only the backend service account may call it) =="
# Provider credentials live in Secret Manager only. Any of these that exist are wired in automatically (and the
# notification service account is allowed to read them):
#   resend-api-key, twilio-account-sid, twilio-auth-token (+ sendgrid-api-key, smtp-password if you use them)
NOTIFY_SECRETS="NOTIFICATION_API_TOKEN=notification-token:latest"
have_secret() { gcloud secrets describe "$1" >/dev/null 2>&1; }
wire_secret() {      # ENV_NAME secret-name
  if have_secret "$2"; then
    gcloud secrets add-iam-policy-binding "$2" --member "serviceAccount:$NOTIFY_SA" \
      --role roles/secretmanager.secretAccessor >/dev/null
    NOTIFY_SECRETS="$NOTIFY_SECRETS,$1=$2:latest"
    return 0
  fi
  return 1
}
HAVE_RESEND=0; HAVE_TWILIO=0; HAVE_MSG91=0
wire_secret RESEND_API_KEY resend-api-key && HAVE_RESEND=1
wire_secret MSG91_AUTH_KEY msg91-auth-key && HAVE_MSG91=1
if wire_secret TWILIO_ACCOUNT_SID twilio-account-sid && wire_secret TWILIO_AUTH_TOKEN twilio-auth-token; then HAVE_TWILIO=1; fi
wire_secret SENDGRID_API_KEY sendgrid-api-key || true
wire_secret SMTP_PASSWORD smtp-password || true
[ -n "${EXTRA_NOTIFY_SECRETS:-}" ] && NOTIFY_SECRETS="$NOTIFY_SECRETS,$EXTRA_NOTIFY_SECRETS"
# Pick the providers from what is configured (override with EMAIL_PROVIDER / SMS_PROVIDER / VOICE_PROVIDER in env.local.sh)
[ "$HAVE_RESEND" = 1 ] && : "${EMAIL_PROVIDER:=resend}"
[ "$HAVE_MSG91" = 1 ] && : "${SMS_PROVIDER:=msg91}"
if [ "$HAVE_TWILIO" = 1 ]; then : "${SMS_PROVIDER:=twilio}"; : "${VOICE_PROVIDER:=twilio}"; fi
echo "   e-mail: ${EMAIL_PROVIDER:-console}   sms: ${SMS_PROVIDER:-console}   voice: ${VOICE_PROVIDER:-console}"
gcloud run deploy notification-service \
  --image "$REGISTRY/notification:$TAG" --region "$REGION" --service-account "$NOTIFY_SA" \
  --no-allow-unauthenticated --cpu 1 --memory 256Mi --concurrency 40 --min-instances 0 --max-instances 3 \
  --set-env-vars "^@^ENVIRONMENT=production@LOG_FORMAT=json@ORGANIZATION_NAME=${ORGANIZATION_NAME:-Your Company}@EMAIL_PROVIDER=${EMAIL_PROVIDER:-console}@EMAIL_FROM=${EMAIL_FROM:-}@EMAIL_REPLY_TO=${EMAIL_REPLY_TO:-}@SMS_PROVIDER=${SMS_PROVIDER:-console}@VOICE_PROVIDER=${VOICE_PROVIDER:-console}@MSG91_TEMPLATE_ID=${MSG91_TEMPLATE_ID:-}@MSG91_TEMPLATE_VAR=${MSG91_TEMPLATE_VAR:-message}@TWILIO_FROM_NUMBER=${TWILIO_FROM_NUMBER:-}@TWILIO_MESSAGING_SERVICE_SID=${TWILIO_MESSAGING_SERVICE_SID:-}@TWILIO_VOICE_FROM_NUMBER=${TWILIO_VOICE_FROM_NUMBER:-}@VOICE_LANGUAGE=${VOICE_LANGUAGE:-en-IN}@REQUIRE_TOKEN=true" \
  --set-secrets "$NOTIFY_SECRETS"
NOTIFY_URL="$(gcloud run services describe notification-service --region "$REGION" --format 'value(status.url)')"
gcloud run services add-iam-policy-binding notification-service --region "$REGION" \
  --member "serviceAccount:$BACKEND_SA" --role roles/run.invoker

BACKEND_COMMON=(
  --image "$REGISTRY/backend:$TAG" --region "$REGION" --service-account "$BACKEND_SA"
  --set-env-vars "ENVIRONMENT=production,LOG_FORMAT=json,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION,VERTEX_AI_MODEL=$VERTEX_AI_MODEL,INTERVIEW_MODE=live,GEMINI_LIVE_MODEL=${GEMINI_LIVE_MODEL:-gemini-3.8-live},GEMINI_LIVE_LOCATION=${GEMINI_LIVE_LOCATION:-us-central1},STORAGE_BACKEND=gcs,GCS_BUCKET_NAME=$BUCKET,GCS_SIGNING_SERVICE_ACCOUNT=$BACKEND_SA,NOTIFICATION_SERVICE_URL=$NOTIFY_URL,NOTIFICATION_AUTH_MODE=both,ENABLE_DEBUG_ENDPOINTS=false,PUBLIC_APPLY_ENABLED=false,REQUIRE_UNLOCK=true,UNLOCK_CREDIT_COST=${UNLOCK_CREDIT_COST:-10},AUTO_CREATE_SCHEMA=false,PRELOAD_MODELS=${PRELOAD_MODELS:-false},CORS_ORIGINS=${CORS_ORIGINS:-http://localhost:5173},FRONTEND_BASE_URL=${FRONTEND_BASE_URL:-http://localhost:5173},ORGANIZATION_NAME=${ORGANIZATION_NAME:-Your Company}"
  --set-secrets "DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest,NOTIFICATION_SERVICE_TOKEN=notification-token:latest"
)

echo "== Database migrations (Cloud Run Job: runs once, never from several instances at the same time) =="
gcloud run jobs deploy interview-migrate "${BACKEND_COMMON[@]}" --set-cloudsql-instances "$CONN" --command alembic --args upgrade,head \
  --max-retries 0 --task-timeout 600
gcloud run jobs execute interview-migrate --region "$REGION" --wait

echo "== Backend service =="
gcloud run deploy interview-backend "${BACKEND_COMMON[@]}" --add-cloudsql-instances "$CONN" \
  --allow-unauthenticated \
  --cpu 2 --memory 4Gi --concurrency 8 --timeout 3600 --min-instances 1 --max-instances 3 \
  --no-cpu-throttling --session-affinity --cpu-boost
BACKEND_URL="$(gcloud run services describe interview-backend --region "$REGION" --format 'value(status.url)')"

echo "== Frontend service =="
gcloud run deploy interview-frontend \
  --image "$REGISTRY/frontend:$TAG" --region "$REGION" --service-account "$FRONTEND_SA" \
  --allow-unauthenticated --cpu 1 --memory 256Mi --concurrency 80 --min-instances 0 --max-instances 5 \
  --set-env-vars "API_URL=$BACKEND_URL"
FRONTEND_URL="$(gcloud run services describe interview-frontend --region "$REGION" --format 'value(status.url)')"

echo "== Point the backend at the real frontend origin (CORS + links in e-mails) =="
gcloud run services update interview-backend --region "$REGION" \
  --update-env-vars "^@^CORS_ORIGINS=$FRONTEND_URL@FRONTEND_BASE_URL=$FRONTEND_URL"
gcloud run jobs update interview-migrate --region "$REGION" --update-env-vars "^@^CORS_ORIGINS=$FRONTEND_URL@FRONTEND_BASE_URL=$FRONTEND_URL"

cat <<MSG

Frontend : $FRONTEND_URL        (admin sign-in: $FRONTEND_URL/admin/login)
Backend  : $BACKEND_URL
Notify   : $NOTIFY_URL  (private)

Still to do once:
  1. Bucket CORS for direct recording uploads:
       sed "s#FRONTEND_ORIGIN#$FRONTEND_URL#" infra/cloudrun/bucket-cors.json > /tmp/cors.json
       gcloud storage buckets update gs://$BUCKET --cors-file=/tmp/cors.json
  2. Create the first admin (password comes from a secret, not the command line):
       printf '%s' 'a-long-random-password' | gcloud secrets create admin-bootstrap-password --data-file=-
       gcloud secrets add-iam-policy-binding admin-bootstrap-password --member serviceAccount:$BACKEND_SA --role roles/secretmanager.secretAccessor
       gcloud run jobs deploy interview-create-admin <same flags as interview-migrate> \\
         --command python --args=-m,scripts.create_admin,--email,you@company.com,--role,admin \\
         --set-secrets ADMIN_PASSWORD=admin-bootstrap-password:latest
       gcloud run jobs execute interview-create-admin --region $REGION --wait
MSG
