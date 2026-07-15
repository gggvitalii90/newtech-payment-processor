#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/newtech-payment-processor}"
PAYMENT_SOURCE="${PAYMENT_SOURCE:-fintablo}"
STAGING_ROOT="${STAGING_ROOT:-/data}"
START_DATE="${START_DATE:-$(date -d 'yesterday' +%F)}"
END_DATE="${END_DATE:-$(date +%F)}"
DRY_RUN_ARGS=()
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  DRY_RUN_ARGS+=("--dry-run" "--no-telegram")
fi

cd "$APP_DIR"

docker compose run --rm daily python scripts/run_daily_update.py \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --payment-source "$PAYMENT_SOURCE" \
  --staging-root "$STAGING_ROOT" \
  "${DRY_RUN_ARGS[@]}"
