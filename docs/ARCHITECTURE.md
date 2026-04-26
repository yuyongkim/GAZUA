# GAZUA Architecture

> 가즈아에 데이터를 얹다 — 그 데이터의 흐름.

---

## 시스템 구성

```
┌──────────────────────────────────────────────────────────────────────┐
│                          DATA LAYER                                   │
│                                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │ FRED · ECOS  │  │ KOSIS · 관세청│  │ DART · KIS   │  ... 18 src   │
│  │ (1990~)      │  │ (1995~)      │  │ (2000~)      │               │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │
│         └──────────────┬───┴────────────────┘                        │
│                        ▼                                              │
│  packages/adapters/ (어댑터 18종)                                     │
│   + 청크/페이지네이션 wrapper 3종 (customs/KIS/KOSIS)                │
│                        ▼                                              │
│  ~/.gazua/data/sectors/  (1.2GB, 1751 CSV)                           │
│                        ▼                                              │
│  packages/core/sectors/registry.py  ← 340 cells 정의                 │
└────────────────────────┬─────────────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       ANALYSIS LAYER                                  │
│                                                                       │
│  packages/core/scoring/  (18 모듈)                                    │
│                                                                       │
│  매크로            종목                 백테스트                      │
│  ──────            ──────              ──────                        │
│  · sector_strength · leader_stock      · sector_rotation_backtest    │
│  · sector_leadlag  · stock_matrix      · leverage_strategy           │
│  · macro_cycle     · pead              · ml_forecast                 │
│  · alerts          · alpha_library     · global_matrix               │
│  · events          · flow_macro                                      │
│                                                                       │
│  운영                                                                 │
│  ──────                                                              │
│  · briefing  · llm_ask  · paper_trade  · quality_monitor             │
└────────────────────────┬─────────────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          API LAYER                                    │
│                                                                       │
│  apps/api/  (FastAPI, 24 endpoint)                                   │
│   /matrix · /strength · /leaders · /backtest · /cycle · /alerts ·    │
│   /briefing · /ask · /flow_macro · /pead · /paper · /strategy · ...  │
└────────────────────────┬─────────────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         FRONTEND LAYER                                │
│                                                                       │
│  apps/web/  (Next.js 14)                                             │
│   /research/sector-indicators                                         │
│     ├ StrengthRanking  (TOP/BOT 5)                                   │
│     ├ BacktestPanel    (3 strategies × NAV chart)                    │
│     ├ LeaderStocksPanel (TOP 20 종목)                                │
│     ├ MatrixHeatmap    (34 × 10 그리드)                              │
│     └ CellDrawer       (시계열 + 종목 리스트)                        │
│                                                                       │
│   /briefing/today  /strategies  /portfolio/paper  ...               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 데이터 흐름

### EOD 사이클
```
17:00 KST  →  scripts/eod_collect.sh full  (Windows Task Scheduler)
                ↓
              18 어댑터 sync  (30~60분)
                ↓
              ~/.gazua/data/sectors/  갱신
                ↓
              quality_monitor 자동 audit  →  ky.db
                ↓
              briefing (10분 캐시 자동 invalidate)
                ↓
              [Cloud only] 카톡/Slack push
```

### 사용자 요청 사이클
```
사용자 → /research/sector-indicators 페이지
         ↓
       매트릭스 API (5분 캐시) → 340 cells JSON
         ↓
       히트맵 렌더 + 셀 클릭
         ↓
       /series/{cell_id} → 30년 시계열 chart
       /symbols?sector=  → 그 섹터 KRX 종목 (ky.db universe 4516개)
```

---

## 핵심 설계 결정

### 1. Stdlib 우선
- pandas / scikit-learn / xgboost 의존성 최소화
- core scoring은 stdlib만으로 동작 (배포 단순화)
- ML / DL은 옵션 dependency (cloud 영역에서 활용)

### 2. CSV-first storage
- raw data는 CSV (가독성·복구·디버깅 편의)
- 캐시·index는 sqlite (ky.db: universe / ohlcv / paper_orders / quality_snapshots)
- PostgreSQL 옵션 (cloud 또는 대규모 운영 시)

### 3. 5분 / 10분 캐시 layer
- API matrix endpoint: 5분
- briefing: 10분
- disk index: 5분
- 사용자 traffic 부담 ↓ + 데이터 신선도 균형

### 4. PID-aware 운영
- `scripts/server.sh` — 다른 python 프로세스에 영향 없이 ky 서버만 관리
- port-aware 헬스체크 (npx wrapper 한계 우회)

### 5. Open Core
- 이 repo (Apache 2.0): self-host 가능
- gazua-cloud (private, BSL 1.1): hosted SaaS 차별화 영역
- 3년 후 BSL → Apache 자동 전환 (Sentry pattern)

---

## 데이터 깊이 검증

```
$ python scripts/verify_cells_v4.py
Total cells: 340
Filled:      334 = 98.2%
Underbacked: 6 (안정 무료 API 부재 — 본질적 ceiling)

$ du -sh ~/.gazua/data/sectors/
1.2G    ~/.gazua/data/sectors/

$ ls ~/.gazua/data/sectors/ | xargs -I{} du -sh ~/.gazua/data/sectors/{}
33 files  customs/        1.0 GB
468 files dart/             9 MB
19 files  fred/             3 MB
... (총 1751 CSV, 17 source folders)
```

---

## 백테스트 무결성

모든 backtest 모듈은 다음 규칙을 강제:

1. **No look-ahead** — 시점 t의 신호는 t-1까지의 데이터만 사용
2. **거래비용** — 0.18% 매도세 + 0.015% 수수료 + 0.1% 슬리피지 항상 반영
3. **Survivorship** — 71 corps DART는 현재 상장사. 백테스트 결과에 명시
4. **IS/OOS 분리** — 1995~2015 train, 2016~ test (sector_rotation_backtest)
5. **다중 시드 검증** — K-means 등 stochastic 모듈은 seed=42 고정

위반 PR은 reject.

---

## 보안

### 시크릿
- `~/.gazua/shared.env` — API keys (FRED, ECOS, KOSIS, DART, KIS 등)
- `.gitignore` 처리, 절대 commit 금지
- LICENSE 위반 / 키 누출 시 즉시 revoke

### KIS 자동주매 (cloud only)
- paper_trade 단계 A: 종이 거래만 (이 repo 포함)
- 단계 B: paper trade with real prices
- 단계 C: 실거래 (gazua-cloud 별도, 사용자 명시 동의 + 일일 한도 + 손실 limit)

---

## 확장 포인트

| 위치 | 확장 가능 |
|---|---|
| `packages/adapters/` | 새 데이터 source (PR 환영) |
| `packages/core/scoring/` | 새 분석 모듈 |
| `packages/core/sectors/registry.py` | 매트릭스 cells 추가/수정 |
| `apps/web/components/` | 새 시각화 패널 |
| `examples/` | Telegram bot, n8n workflow 등 |

---

자세한 ROADMAP은 [ROADMAP.md](ROADMAP.md), 매트릭스 정의는 [INDICATORS.md](INDICATORS.md).
