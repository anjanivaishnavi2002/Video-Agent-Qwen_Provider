#!/bin/sh
# Writes the runtime configuration the React app loads before it starts (see src/main.jsx).
# Only API_URL (a public URL) goes here - never put secrets in the frontend.
set -e
API_ESCAPED=$(printf '%s' "${API_URL:-}" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '{"API_URL":"%s"}\n' "$API_ESCAPED" > /usr/share/nginx/html/config.json
