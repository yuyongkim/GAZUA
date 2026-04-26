# GAZUA Roadmap

> ky-platform (사내 풀스택)에서 OSS 가능한 부분을 점진적으로 GAZUA로 추출.
> 위트 카피·마케팅·premium 기능은 별도 `gazua-cloud` (private)에서.

## Phase 0 — Bootstrap (현재)
- [x] Repo + LICENSE (Apache 2.0) + README
- [ ] CI (GitHub Actions): lint + tests
- [ ] CONTRIBUTING.md
- [ ] CODE_OF_CONDUCT.md

## Phase 1 — Core 추출 (ky-platform → GAZUA/packages/core)
- [ ] `packages/core/scoring/macro_sector_strength.py`
- [ ] `packages/core/scoring/leader_stock.py`
- [ ] `packages/core/scoring/sector_rotation_backtest.py`
- [ ] `packages/core/scoring/macro_cycle.py`
- [ ] `packages/core/scoring/alerts.py`
- [ ] `packages/core/sectors/registry.py` (매트릭스 정의)
- [ ] `docs/INDICATORS.md` (340 cells 매핑)

## Phase 2 — Adapter 추출 (무료 데이터 소스만)
- [ ] FRED · ECOS · KOSIS · OECD · World Bank
- [ ] customs (data.go.kr) · CFTC · UN Comtrade
- [ ] yfinance · openFDA · stooq
- [ ] EIA
- ⚠ 제외: KIS (broker — 사용자 자체 키 필요), DART (corp_code 일부 비공개)

## Phase 3 — API 추출
- [ ] `apps/api` FastAPI — 11~24개 endpoint 중 OSS-safe만
- [ ] `apps/api/middleware` — auth는 stub만 (cloud에서 주입)

## Phase 4 — Web 추출
- [ ] `apps/web` Next.js dashboard
- [ ] 매트릭스 히트맵 + 셀 drawer + 강도 ranking
- [ ] 페이지별 위트 카피는 페이지 만들면서 정함 (한 번에 풀로 안 채움)

## Phase 5 — Self-host 가이드
- [ ] `docker-compose.yml` (web + api + sqlite)
- [ ] `examples/telegram_bot.py`
- [ ] `examples/daily_briefing.py`
- [ ] `docs/SELF_HOST.md`

## Phase 6 — Community
- [ ] GitHub Discussions enable
- [ ] 이슈 템플릿
- [ ] PR 템플릿
- [ ] 첫 release v0.1.0

---

## 추출 원칙

1. **OSS-safe만** — broker key / corp_code 전체 / private API key 없는 것
2. **사용자 BYO 데이터** — 각자 무료 API 키 발급해서 사용
3. **Premium은 cloud로** — Slack/email 자동 알림, 학습된 ML, hosted DB 등은
   `gazua-cloud` (private repo) 에서만
4. **위트 카피는 점진적** — 페이지 만들 때 그 자리 어울리는 걸로. 한 번에
   카피 풀 만들지 않음.
5. **License 통일** — Apache 2.0 (코어), BSL 1.1 (premium 영역, cloud repo)

---

## 스택

- Backend: Python 3.13 + FastAPI + SQLAlchemy + sqlite (default) / PostgreSQL (option)
- Frontend: Next.js 14 + TypeScript + Tailwind
- Data: ~/.gazua/data/ (사용자 home, .gitignored)
- Auth (cloud only): Clerk
- Billing (cloud only): Stripe
