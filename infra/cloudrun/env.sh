# Shared settings for setup.sh / deploy.sh. Copy to env.local.sh, edit, and `source` it. NO secrets in this file.
export PROJECT_ID="${PROJECT_ID:-my-gcp-project}"
export REGION="${REGION:-us-central1}"            # Cloud Run, Cloud SQL, Artifact Registry, Vertex AI region
export VERTEX_LOCATION="${VERTEX_LOCATION:-us-central1}"
export VERTEX_AI_MODEL="${VERTEX_AI_MODEL:-gemini-2.5-flash}"

export AR_REPO="${AR_REPO:-interview}"                           # Artifact Registry repository
export BUCKET="${BUCKET:-${PROJECT_ID}-interview-files}"         # private bucket: resumes + recordings
export SQL_INSTANCE="${SQL_INSTANCE:-interview-db}"
export SQL_DB="${SQL_DB:-interview}"
export SQL_USER="${SQL_USER:-interview_app}"

export BACKEND_SA="interview-backend@${PROJECT_ID}.iam.gserviceaccount.com"
export NOTIFY_SA="interview-notify@${PROJECT_ID}.iam.gserviceaccount.com"
export FRONTEND_SA="interview-frontend@${PROJECT_ID}.iam.gserviceaccount.com"
export LLM_GW_SA="interview-llm-gateway@${PROJECT_ID}.iam.gserviceaccount.com"

export REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}"
export TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"

# ---- Notifications (non-secret values; the API keys/tokens live in Secret Manager) ----------------------------
# Secrets deploy.sh picks up automatically if they exist: resend-api-key, twilio-account-sid, twilio-auth-token
# export EMAIL_FROM="Recruiting <hr@your-verified-domain.com>"     # domain must be verified in Resend
# export EMAIL_REPLY_TO=""
# export TWILIO_FROM_NUMBER="+1XXXXXXXXXX"                          # or: export TWILIO_MESSAGING_SERVICE_SID="MG..."
# export VOICE_LANGUAGE="en-IN"

# ---- LLM gateway (see docs/LLM_GATEWAY.md) --------------------------------------------------------------------
# Keys live in Secret Manager as openai-api-key, anthropic-api-key, gemini-api-key, deepseek-api-key (override the names
# with OPENAI_SECRET, ANTHROPIC_SECRET, GEMINI_API_SECRET, DEEPSEEK_SECRET if yours differ).
# export LLM_GATEWAY_SHARE=50                          # % of requests sent to the gateway first (0 = only when Vertex fails)
# export GATEWAY_WEIGHTS="openai:1,anthropic:1,gemini_api:1,deepseek:1"   # how the gateway spreads across its providers
# export GATEWAY_EXCLUDE=""                            # e.g. "deepseek" to never send candidate data to that provider
# export OPENAI_MODEL=gpt-4o-mini ANTHROPIC_MODEL=claude-sonnet-4-5 GEMINI_API_MODEL=gemini-2.5-flash DEEPSEEK_MODEL=deepseek-chat
