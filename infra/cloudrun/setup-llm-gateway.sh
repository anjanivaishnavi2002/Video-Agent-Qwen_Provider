#!/usr/bin/env bash
# ONE-TIME setup for the LLM gateway: its service account, its access token, and the provider API-key secrets.
# Safe to run more than once (it never overwrites an existing key). Does NOT deploy anything.
#   cd infra/cloudrun && source env.local.sh && bash setup-llm-gateway.sh
set -euo pipefail
: "${PROJECT_ID:?source env.local.sh first}"
gcloud config set project "$PROJECT_ID"
LLM_GW_SA="${LLM_GW_SA:-interview-llm-gateway@${PROJECT_ID}.iam.gserviceaccount.com}"
BACKEND_SA="${BACKEND_SA:-interview-backend@${PROJECT_ID}.iam.gserviceaccount.com}"

echo "== Service account =="
gcloud iam service-accounts describe "$LLM_GW_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create interview-llm-gateway --display-name "LLM gateway"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$LLM_GW_SA" \
  --role roles/logging.logWriter --condition=None >/dev/null

echo "== Access token (backend -> gateway) =="
if gcloud secrets describe llm-gateway-token >/dev/null 2>&1; then
  echo "llm-gateway-token already exists, leaving it as is"
else
  python3 -c 'import secrets;print(secrets.token_urlsafe(32),end="")' | gcloud secrets create llm-gateway-token --replication-policy automatic --data-file=-
fi
for sa in "$LLM_GW_SA" "$BACKEND_SA"; do
  gcloud secrets add-iam-policy-binding llm-gateway-token --member "serviceAccount:$sa" \
    --role roles/secretmanager.secretAccessor >/dev/null
done

echo "== Provider API keys (typed at a hidden prompt, stored only in Secret Manager; press Enter to skip one) =="
add_key() {      # secret-name  label
  if gcloud secrets describe "$1" >/dev/null 2>&1; then echo "$1 already exists - keeping it"; return; fi
  read -r -s -p "$2 API key: " K; echo
  if [ -n "$K" ]; then printf '%s' "$K" | gcloud secrets create "$1" --replication-policy automatic --data-file=-; fi
  unset K
}
add_key "${OPENAI_SECRET:-openai-api-key}"       "OpenAI"
add_key "${ANTHROPIC_SECRET:-anthropic-api-key}" "Anthropic"
add_key "${GEMINI_API_SECRET:-gemini-api-key}"   "Gemini API (AI Studio)"
add_key "${DEEPSEEK_SECRET:-deepseek-api-key}"   "DeepSeek"
echo "Done. Secrets now in the project:"; gcloud secrets list --format="value(name)" | sed 's/^/  /'
echo "Next: bash deploy.sh"
