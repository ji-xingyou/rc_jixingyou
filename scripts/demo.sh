#!/usr/bin/env bash
set -euo pipefail

api_url="${NOTIFY_DEMO_URL:-http://127.0.0.1:8080}"
target_url="${NOTIFY_DEMO_TARGET:-http://webhook-sink:8080/demo}"

curl --fail-with-body -i "${api_url}/v1/notifications" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$(date +%s)" \
  --data "{\"target_url\":\"${target_url}\",\"body\":{\"event\":\"demo\"}}"

