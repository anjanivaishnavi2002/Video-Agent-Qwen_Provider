#!/bin/sh
# Starts the API. Cloud Run sets $PORT. One worker on purpose: live interview state is held in memory
# (it is rebuilt from the database if an instance restarts); scale with instances, not workers.
set -e

# Optional: apply database migrations before starting. For production prefer a Cloud Run Job
# (see docs/DEPLOYMENT.md) so several instances never migrate at the same time.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "Running database migrations..."
  alembic upgrade head
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips='*' \
  --timeout-graceful-shutdown "${GRACEFUL_SHUTDOWN_SECONDS:-20}" \
  --no-server-header
