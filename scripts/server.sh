#!/usr/bin/env bash
# ky-platform PID-aware dev server controller.
# api (uvicorn) + web (next dev) — 각 PID는 .run/*.pid에 기록되어 ky-platform 서버만
# 정확히 stop/restart 가능 (다른 python 프로세스에 영향 없음).
#
# Usage:
#   scripts/server.sh start       — start api + web in background, write PIDs
#   scripts/server.sh stop        — stop ky-platform api + web only (read PIDs)
#   scripts/server.sh restart     — stop then start
#   scripts/server.sh status      — print PID + alive status + URLs
#   scripts/server.sh logs api    — tail api log
#   scripts/server.sh logs web    — tail web log

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/runtime_logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

API_PID_FILE="$RUN_DIR/api.pid"
WEB_PID_FILE="$RUN_DIR/web.pid"
API_LOG="$LOG_DIR/api.log"
WEB_LOG="$LOG_DIR/web.log"

# Load shared.env if present so adapters get API keys.
SHARED="$HOME/.gazua/shared.env"
if [ -f "$SHARED" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$SHARED"
  set +a
fi

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8300}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-8380}"

is_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# Port-based liveness check (npx/npm wrappers obscure the real PID).
# Returns 0 if the port is bound, 1 otherwise.
port_alive() {
  local port="$1"
  # Try ss → lsof → netstat fallback
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$port\$"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -i ":$port" -sTCP:LISTEN 2>/dev/null | grep -q LISTEN
  else
    netstat -an 2>/dev/null | grep -q ":$port .*LISTEN"
  fi
}

# PID listening on a port (Windows: netstat -ano + tasklist; *nix: lsof).
pid_on_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti ":$port" -sTCP:LISTEN 2>/dev/null | head -1
  elif [ "$(uname -o 2>/dev/null)" = "Msys" ] || uname -s | grep -qi mingw; then
    netstat -ano 2>/dev/null | awk -v p=":$port" '$2 ~ p && /LISTENING/ {print $5; exit}'
  fi
}

read_pid() {
  local f="$1"
  [ -f "$f" ] && cat "$f" 2>/dev/null || echo ""
}

cmd_start() {
  # API
  local api_pid
  api_pid="$(read_pid "$API_PID_FILE")"
  if is_alive "$api_pid"; then
    echo "[server] api already running (PID $api_pid)"
  else
    echo "[server] starting api on http://$API_HOST:$API_PORT"
    (
      cd "$ROOT/apps/api"
      nohup python -m uvicorn main:app --host "$API_HOST" --port "$API_PORT" \
        > "$API_LOG" 2>&1 &
      echo $! > "$API_PID_FILE"
    )
    sleep 0.5
    api_pid="$(read_pid "$API_PID_FILE")"
    echo "[server] api started (PID $api_pid)"
  fi

  # WEB
  local web_pid
  web_pid="$(read_pid "$WEB_PID_FILE")"
  if is_alive "$web_pid"; then
    echo "[server] web already running (PID $web_pid)"
  else
    echo "[server] starting web on http://$WEB_HOST:$WEB_PORT"
    (
      cd "$ROOT/apps/web"
      # next dev directly so the PID we capture is the actual server (not the npm wrapper).
      nohup npx next dev -p "$WEB_PORT" -H "$WEB_HOST" \
        > "$WEB_LOG" 2>&1 &
      echo $! > "$WEB_PID_FILE"
    )
    sleep 0.5
    web_pid="$(read_pid "$WEB_PID_FILE")"
    echo "[server] web started (PID $web_pid)"
  fi

  echo ""
  echo "[server] URLs:"
  echo "  api:  http://$API_HOST:$API_PORT/docs"
  echo "  web:  http://$WEB_HOST:$WEB_PORT/research/sector-indicators"
  echo ""
  echo "[server] logs: $API_LOG / $WEB_LOG"
}

cmd_stop() {
  local api_pid web_pid
  api_pid="$(read_pid "$API_PID_FILE")"
  web_pid="$(read_pid "$WEB_PID_FILE")"

  if is_alive "$api_pid"; then
    echo "[server] stopping api (PID $api_pid)"
    kill "$api_pid" 2>/dev/null || true
    sleep 0.3
    is_alive "$api_pid" && kill -9 "$api_pid" 2>/dev/null || true
  else
    echo "[server] api not running"
  fi
  rm -f "$API_PID_FILE"

  if is_alive "$web_pid"; then
    echo "[server] stopping web (PID $web_pid)"
    kill "$web_pid" 2>/dev/null || true
    sleep 0.3
    is_alive "$web_pid" && kill -9 "$web_pid" 2>/dev/null || true
    # next dev spawns child; ensure tree is gone (best-effort)
    pkill -P "$web_pid" 2>/dev/null || true
  else
    echo "[server] web not running"
  fi
  rm -f "$WEB_PID_FILE"
}

cmd_status() {
  local api_pid web_pid
  api_pid="$(read_pid "$API_PID_FILE")"
  web_pid="$(read_pid "$WEB_PID_FILE")"

  # Liveness: PID alive OR port bound (web-via-npx obscures real PID).
  local api_state web_state
  if is_alive "$api_pid" || port_alive "$API_PORT"; then api_state="yes"; else api_state="no"; fi
  if is_alive "$web_pid" || port_alive "$WEB_PORT"; then web_state="yes"; else web_state="no"; fi

  printf "%-6s %-10s %-10s %s\n" "svc" "pid" "alive" "url"
  printf "%-6s %-10s %-10s %s\n" "---" "---" "---" "---"
  printf "%-6s %-10s %-10s %s\n" "api" "${api_pid:-—}" "$api_state" \
    "http://$API_HOST:$API_PORT/docs"
  printf "%-6s %-10s %-10s %s\n" "web" "${web_pid:-—}" "$web_state" \
    "http://$WEB_HOST:$WEB_PORT/research/sector-indicators"
}

cmd_logs() {
  local svc="${1:-api}"
  case "$svc" in
    api) tail -n 80 -f "$API_LOG" ;;
    web) tail -n 80 -f "$WEB_LOG" ;;
    *)   echo "usage: $0 logs {api|web}" ; exit 1 ;;
  esac
}

cmd_restart() {
  cmd_stop
  sleep 0.5
  cmd_start
}

case "${1:-status}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    cmd_logs "${2:-api}" ;;
  *)       echo "usage: $0 {start|stop|restart|status|logs api|logs web}" ; exit 1 ;;
esac
