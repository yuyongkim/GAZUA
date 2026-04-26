#!/usr/bin/env bash
# EOD scheduled collection — 매일 장 마감 후 (KST 16:00~17:00) 실행 권장.
# 모든 30년 백필 정책이 적용된 출처를 일괄 새로고침하여
# matrix endpoint의 5분 캐시 invalidate 후 재계산되도록 함.
#
# Usage:
#   scripts/eod_collect.sh                  — 전체 collect (기본)
#   scripts/eod_collect.sh fast             — 가벼운 출처만 (FRED/ECOS/KIS/yf)
#   scripts/eod_collect.sh sources fred ecos
#
# Cron 등록 (Linux/macOS):
#   0 17 * * 1-5 /path/to/ky-platform/scripts/eod_collect.sh >> /tmp/ky-eod.log 2>&1
#
# Windows Task Scheduler 등록:
#   scripts/eod_collect.ps1 (별도)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/runtime_logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/eod_$(date +%Y%m%d).log"

# Load shared.env so adapters get API keys
SHARED="$HOME/.gazua/shared.env"
if [ -f "$SHARED" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$SHARED"
  set +a
fi

cd "$ROOT"

mode="${1:-full}"
shift || true

run() {
  local label="$1"; shift
  echo ""
  echo "=== [$label] $(date +%H:%M:%S) ==="
  python "$@" || echo "[!] $label failed (continuing)"
}

case "$mode" in
  fast)
    # Light sources only — quick (<2 min total)
    run fred       scripts/collect_sectors.py --source fred
    run ecos       scripts/collect_sectors.py --source ecos
    run kis_index  scripts/collect_sectors.py --source kis_index
    run yf         scripts/collect_sectors.py --source yf_commodities
    ;;
  sources)
    for src in "$@"; do
      run "$src" scripts/collect_sectors.py --source "$src"
    done
    ;;
  full|*)
    # 전체 — 30~60분 소요. DART/customs는 큰 batch라 마지막에.
    for src in fred ecos kis_index yf_commodities oecd worldbank eia cftc un_comtrade openfda kosis stooq; do
      run "$src" scripts/collect_sectors.py --source "$src"
    done
    # pytrends는 Google rate-limit으로 source-sleep 12s
    run pytrends scripts/collect_sectors.py --source pytrends --source-sleep 12
    # customs/dart는 가장 무거우므로 마지막
    run customs scripts/collect_sectors.py --source customs
    run dart    scripts/collect_sectors.py --source dart
    ;;
esac

# Cell-level verification + matrix cache invalidation hint
echo ""
echo "=== verify $(date +%H:%M:%S) ==="
python scripts/verify_cells_v4.py | head -5

echo ""
echo "=== sector_coverage $(date +%H:%M:%S) ==="
python scripts/sector_coverage.py | tail -5

# API matrix cache: server.sh 가 띄운 api 프로세스를 hup 보내지 않고도 5분 TTL 후
# 자동으로 새 데이터 반영. 즉시 invalidate가 필요하면 server.sh restart.

echo ""
echo "=== EOD complete at $(date +%H:%M:%S) ==="
echo "log: $LOG_FILE"
