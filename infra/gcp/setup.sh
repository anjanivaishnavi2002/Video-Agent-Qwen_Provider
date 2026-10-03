#!/usr/bin/env bash
# One-time GCP setup for the AI Video Interview Agent (Compute Engine VM + Cloud SQL + Cloud Storage).
#
# READ IT BEFORE RUNNING. It creates billable resources. It was written from the gcloud
# documentation and has not been executed against your project.
#
#   export PROJECT_ID=my-project GITHUB_REPO=my-org/video-agent DOMAIN=interview.example.com
#   bash infra/gcp/setup.sh
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${GITHUB_REPO:?set GITHUB_REPO (owner/name)}"
REGION="${REGION:-europe-west1}"
ZONE="${ZONE:-europe-west1-b}"
VM_NAME="${VM_NAME:-video-agent-vm}"
# CPU-only default. For a GPU VM use e.g. MACHINE_TYPE=g2-standard-8 GPU=nvidia-l4 and the
# Deep Learning VM image below (NVIDIA driver preinstalled).
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-8}"
GPU="${GPU:-}"
AR_REPO="${AR_REPO:-video-agent}"
BUCKET="${BUCKET:-${PROJECT_ID}-video-agent}"
SQL_INSTANCE="${SQL_INSTANCE:-video-agent-db}"
DB_NAME="${DB_NAME:-video_agent}"
DB_USER="${DB_USER:-video_agent}"
VM_SA="video-agent-vm"
DEPLOY_SA="video-agent-deploy"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "== APIs"
gcloud services enable compute.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com iap.googleapis.com servicenetworking.googleapis.com storage.googleapis.com

echo "== Artifact Registry"
gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$AR_REPO" --repository-format docker --location "$REGION"

echo "== Cloud Storage bucket (private)"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention

echo "== Service accounts"
gcloud iam service-accounts describe "$VM_SA@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$VM_SA" --display-name "Video Agent VM"
gcloud iam service-accounts describe "$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$DEPLOY_SA" --display-name "Video Agent GitHub deploy"

for role in roles/cloudsql.client roles/artifactregistry.reader roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$VM_SA@$PROJECT_ID.iam.gserviceaccount.com" --role "$role" --condition=None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$VM_SA@$PROJECT_ID.iam.gserviceaccount.com" --role roles/storage.objectAdmin >/dev/null

for role in roles/artifactregistry.writer roles/compute.instanceAdmin.v1 roles/iap.tunnelResourceAccessor roles/compute.osAdminLogin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com" --role "$role" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$VM_SA@$PROJECT_ID.iam.gserviceaccount.com" \
  --member "serviceAccount:$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com" --role roles/iam.serviceAccountUser >/dev/null

echo "== Workload Identity Federation for GitHub Actions"
gcloud iam workload-identity-pools describe github --location global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create github --location global --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe github-provider --location global --workload-identity-pool github >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc github-provider --location global \
    --workload-identity-pool github --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='${GITHUB_REPO}'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPO}" >/dev/null

echo "== Private networking for Cloud SQL"
gcloud compute addresses describe google-managed-services-default --global >/dev/null 2>&1 || \
  gcloud compute addresses create google-managed-services-default --global --purpose VPC_PEERING --prefix-length 16 --network default
gcloud services vpc-peerings connect --service servicenetworking.googleapis.com \
  --ranges google-managed-services-default --network default 2>/dev/null || true

echo "== Cloud SQL (PostgreSQL 16, private IP)"
DB_PASSWORD="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 28)"
gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql instances create "$SQL_INSTANCE" --database-version POSTGRES_16 --tier db-custom-2-7680 \
    --region "$REGION" --network default --no-assign-ip --availability-type zonal --backup-start-time 03:00
gcloud sql databases describe "$DB_NAME" --instance "$SQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql databases create "$DB_NAME" --instance "$SQL_INSTANCE"
gcloud sql users create "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD" 2>/dev/null || \
  gcloud sql users set-password "$DB_USER" --instance "$SQL_INSTANCE" --password "$DB_PASSWORD"

echo "== Network: static IP + firewall"
gcloud compute addresses describe video-agent-ip --region "$REGION" >/dev/null 2>&1 || \
  gcloud compute addresses create video-agent-ip --region "$REGION"
gcloud compute firewall-rules describe video-agent-web >/dev/null 2>&1 || \
  gcloud compute firewall-rules create video-agent-web --allow tcp:80,tcp:443 --target-tags video-agent
gcloud compute firewall-rules describe video-agent-iap-ssh >/dev/null 2>&1 || \
  gcloud compute firewall-rules create video-agent-iap-ssh --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags video-agent

echo "== VM"
if [ -n "$GPU" ]; then
  IMAGE_ARGS=(--image-family common-cu124-ubuntu-2204-nvidia-550 --image-project deeplearning-platform-release
              --accelerator "type=${GPU},count=1" --maintenance-policy TERMINATE --metadata install-nvidia-driver=True)
else
  IMAGE_ARGS=(--image-family ubuntu-2204-lts --image-project ubuntu-os-cloud)
fi
gcloud compute instances describe "$VM_NAME" --zone "$ZONE" >/dev/null 2>&1 || \
  gcloud compute instances create "$VM_NAME" --zone "$ZONE" --machine-type "$MACHINE_TYPE" \
    --boot-disk-size 120GB --boot-disk-type pd-balanced "${IMAGE_ARGS[@]}" \
    --service-account "$VM_SA@$PROJECT_ID.iam.gserviceaccount.com" --scopes cloud-platform \
    --address video-agent-ip --tags video-agent \
    --metadata-from-file startup-script=infra/gcp/vm-startup.sh

SQL_CONN="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"
IP="$(gcloud compute addresses describe video-agent-ip --region "$REGION" --format='value(address)')"
ENC_PASSWORD="$DB_PASSWORD"   # alphanumeric only, so no URL-encoding needed

cat <<OUT

================ DONE ================
1) DNS: create an A record   ${DOMAIN:-interview.example.com}  ->  ${IP}

2) GitHub repo -> Settings -> Secrets and variables -> Actions
   Secrets:
     GCP_WORKLOAD_IDENTITY_PROVIDER = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider
     GCP_SERVICE_ACCOUNT            = ${DEPLOY_SA}@${PROJECT_ID}.iam.gserviceaccount.com
   Variables:
     GCP_PROJECT=${PROJECT_ID}  GCP_REGION=${REGION}  GCP_ZONE=${ZONE}
     GCP_VM_NAME=${VM_NAME}     AR_REPO=${AR_REPO}

3) On the VM create /opt/video-agent/.env  (from .env.example) with:
     REGISTRY=${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}
     TAG=latest
     SITE_ADDRESS=${DOMAIN:-interview.example.com}
     CLOUDSQL_INSTANCE=${SQL_CONN}
     DATABASE_URL=postgresql+psycopg2://${DB_USER}:${ENC_PASSWORD}@cloudsql-proxy:5432/${DB_NAME}
     STORAGE_BACKEND=gcs
     GCS_BUCKET=${BUCKET}
     ADMIN_API_KEY=$(openssl rand -hex 24)
     CORS_ORIGINS=https://${DOMAIN:-interview.example.com}
     OLLAMA_MODEL=qwen2.5:3b-instruct
     WHISPER_MODEL=base
   (gcloud compute ssh ${VM_NAME} --zone ${ZONE} --tunnel-through-iap)

4) Push to main -> GitHub Actions builds, pushes and deploys.
OUT
