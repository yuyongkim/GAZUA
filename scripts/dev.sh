#!/usr/bin/env bash
# ky-platform dev runner — starts api (8300) + web (8380) concurrently.
# Load order: shared.env → apps/api/.env → shell env.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 1. shared secrets (if present)
SHARED="$HOME/.gazua/shared.env"
if [ -f "$SHARED" ]; then
  echo "[dev.sh] loading $SHARED"
  set -a
  # shellcheck disable=SC1090
  source "$SHARED"
  set +a
else
  echo "[dev.sh] note: $SHARED not found — external API calls will be skipped"
fi

# 2. local .env (for port overrides etc.) is loaded by config.py / next.config.ts
cd "$ROOT"

# 3. launch
cleanup() {
  echo "[dev.sh] stopping..."
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[dev.sh] starting api on :${API_PORT:-8300}"
( cd "$ROOT/apps/api" && uvicorn main:app --reload --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8300}" ) &

echo "[dev.sh] starting web on :${WEB_PORT:-8380}"
( cd "$ROOT/apps/web" && npm run dev -- -p "${WEB_PORT:-8380}" -H "${WEB_HOST:-127.0.0.1}" ) &

wait
