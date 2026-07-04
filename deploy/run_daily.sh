#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/newtech-payment-processor}"
PAYMENT_SOURCE="${PAYMENT_SOURCE:-max}"
START_DATE="${START_DATE:-$(date -d 'yesterday' +%F)}"
END_DATE="${END_DATE:-$(date +%F)}"

cd "$APP_DIR"

docker compose run --rm daily python scripts/run_daily_update.py \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --payment-source "$PAYMENT_SOURCE"
