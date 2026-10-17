# LLM gateway

A separate private Cloud Run service (`llm-gateway`, folder `llm-gateway/`) that holds the OpenAI, Anthropic, Gemini-API-key
and DeepSeek keys. The backend keeps **Gemini on Vertex AI as its primary model** and uses the gateway two ways:

* **Fallback**: if Vertex is overloaded, rate-limited, unreachable or mis-configured, the request goes to the gateway.
  A safety block from a model is *not* sent to another model.
* **Spread**: `LLM_GATEWAY_SHARE` (0-100) is the percentage of requests sent to the gateway *first*. The gateway spreads them
  across its providers by weight (`GATEWAY_WEIGHTS`), skips a provider that just failed for `GATEWAY_COOLDOWN_SECONDS`, and fails
  over to the others. Each side (Vertex / gateway) is the other's fallback. `0` = gateway only when Vertex fails.

```
backend --(token + Cloud Run IAM)--> llm-gateway --> openai | anthropic | gemini_api | deepseek
   |__ Vertex AI (direct, ADC) is tried first for (100 - SHARE)% of requests, and as the fallback for the rest
```

The Gemini **Live** voice interview is unchanged (it stays on Vertex).

## One-time setup, then deploy (Cloud Shell, folder `infra/cloudrun`)

```
source env.local.sh
bash setup-llm-gateway.sh     # service account, access token, hidden prompts for any missing API keys
bash deploy.sh                # builds + deploys llm-gateway, then points the backend at it
```

`deploy.sh` skips the gateway cleanly when `setup-llm-gateway.sh` has not been run. Secret names default to `openai-api-key`,
`anthropic-api-key`, `gemini-api-key`, `deepseek-api-key` (override with `OPENAI_SECRET`, ... in `env.local.sh`).

## Settings (put them in `env.local.sh`; a deploy re-applies them)

| Variable | Meaning |
|---|---|
| `LLM_GATEWAY_SHARE` | % of requests the gateway takes first (deploy default 50; 0 = fallback only) |
| `GATEWAY_WEIGHTS` | how the gateway spreads across providers, e.g. `openai:2,anthropic:1,gemini_api:1,deepseek:0` |
| `GATEWAY_EXCLUDE` | providers never used, e.g. `deepseek` |
| `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GEMINI_API_MODEL`, `DEEPSEEK_MODEL` | model names (defaults in `llm-gateway/app/config.py`) |

## Things to decide knowingly

* **Candidate data leaves Google.** Interview transcripts and resume text sent through the gateway reach OpenAI, Anthropic, Google's
  Gemini API and DeepSeek (hosted in China). Check this against your consent text and privacy policy; use `GATEWAY_EXCLUDE=deepseek`
  (or weights) to remove a provider.
* **Consistency.** Different models answer different interview turns and write different evaluations. For consistent, comparable
  scoring across candidates, keep `LLM_GATEWAY_SHARE` low (or 0) and let the gateway be a fallback.
* Message content is never logged by the gateway; it logs provider, model, latency, token counts and failure kinds only.

## Checks

* Gateway health: `GET /ready` lists enabled providers. Admin system check (`ping`) now also reports the gateway's providers.
* Logs: `gcloud run services logs read llm-gateway --region $REGION --limit 30` (look for `answered provider=...`).
