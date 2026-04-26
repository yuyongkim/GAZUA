# GAZUA 가즈아

> **가즈아에 데이터를 얹다.**
> 한강 물이 아닌, 한강뷰로 가즈아.

[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-bootstrap-orange)](docs/ROADMAP.md)
[![Coverage](https://img.shields.io/badge/matrix-334%2F340%20%2898.2%25%29-brightgreen)](#cell-level-검증)

> **Status (2026-04-26)**: 사내 풀스택 (`ky-platform`) 완성. OSS 추출 진행 중.
> 본 README는 GAZUA가 자체 호스팅 시 제공할 **전체 기능 청사진**. Phase 1~6 추출 후
> 동일하게 동작 (ROADMAP.md 참조).

---

## 한 화면 요약

```
┌─────────────────────────────────────────────────────────────────────┐
│  WICS 34섹터 × 10지표 × 30년 매트릭스  →  1.2GB / 1,751 CSV          │
│  18 무료 어댑터 (FRED·ECOS·KOSIS·관세청·DART·OECD·EIA·EXIM·CFTC·    │
│   UN Comtrade·yfinance·OpenFDA·Stooq·KIS·Naver·...)                 │
│                            ▼                                         │
│  18 분석 모듈 (scoring/) →  24 API endpoints  →  5 panel UI          │
│                            ▼                                         │
│  자동 EOD 수집  →  일일 브리핑  →  카톡/Slack 알림 (cloud)           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 왜 GAZUA인가

| 한강 물 | 한강뷰 (GAZUA) |
|---|---|
| FOMO · 감 · 뉴스 흘끔 | 매트릭스 z-score · 30년 백테스트 결과 |
| 진입가 후회 | 매수 사유가 적힌 시그널 (예: "macro #3 LME 구리 z=+3.2 / RS=99 / TT=8/8") |
| 손절 못 함 | 룰 기반 -7% 자동 alert |
| "어디 들어가지?" | 섹터 강도 TOP 3 + 그 섹터 종목 매크로 결합 점수 TOP 30 |
| "지금 사이클 뭐지?" | K-means 4-cluster 자동 판정 (회복/확장/둔화/침체) |
| "가즈아!" 무모 | "야수가 사다리를 들면 한강뷰" — 데이터 + 야성 |

> 감정과 야성은 부정하지 않습니다. 결정 **직전**에 데이터 한 장을 끼워 넣을 뿐.

---

## 1. 데이터 자산

### 1.1 매트릭스
- **WICS 34섹터 × 10지표 = 340 cells** (`docs/INDICATORS.md`)
- 30년 시계열 (1995~2026, 출처별 깊이 차등)
- 셀 단위 검증: **334/340 = 98.2%** (남은 6 cells = 안정 무료 API 부재 — 본질적 ceiling)

### 1.2 18 무료 데이터 어댑터

| 출처 | 깊이 | 비고 |
|---|---|---|
| **FRED** (US Fed) | 1990~ (~36년 일별) | macro 800K+ 시리즈 |
| **ECOS** (한국은행) | 1990~ (~36년) | 한국 금리·환율·BSI·CSI |
| **KOSIS** (통계청) | 1990~ (광공업·고용·GDP, 65년 분기) | 청크 wrapper로 40K 셀 한도 우회 |
| **customs** (관세청 TRASS) | 1995~ (HS-level 월별 31년) | 12개월 윈도우를 청크로 우회 |
| **DART** (금감원) | 2000~2025 (71 corps × 26년) | corp_code 검증 + 잘못된 코드 수정 |
| **KIS** (한국투자증권) | 1995~ (업종지수 일별 31년) | 50거래일 한도를 페이지네이션으로 우회 |
| **EIA** (US Energy) | 1986~ (40년) | WTI · Brent · 정제가동률 |
| **OECD** (CLI) | 40년 월별 | 5개국 경기선행지수 |
| **World Bank** | 1990~2025 (36년 연간) | GDP·인구·국방비 |
| **CFTC** (COT) | 1986~ (~38년 weekly) | 원유·금·구리·엔·유로 포지션 |
| **UN Comtrade** | 1995~2024 (30년 annual) | 글로벌 HS cross-check |
| **yfinance / yf_commodities** | period=max (~25-35년) | 한국 sector ETF 23종 (KODEX/TIGER) + 글로벌 commodity |
| **pytrends** (Google Trends) | 2004~ (22년 monthly) | 검색관심도 (rate-limit) |
| **OpenFDA** | 12년 월별 | 의약품 승인 |
| **Stooq** | 보조 | 알루미늄 cash |
| **EXIM** | 환율 일별 | 한국 수출입은행 |
| **Naver / FnGuide** | 한국 재무 (크롤링) | 사용자 BYO |
| **DART corp_code** | 117K corps lookup | 자체 검증 패턴 |

### 1.3 청킹·페이지네이션 wrapper (자체 구현 3종)
- `CustomsAdapter.get_hs_country_history` — 12개월 → 1995~ chunked
- `KISMarketAdapter.get_index_daily_history` — 50거래일 → 페이지네이션
- `KOSISAdapter.get_data_chunked` — 40,000 셀 → start/end 청크

---

## 2. 분석 모듈 (`packages/core/scoring/`) — 18개

### 2.1 매크로 / 섹터 분석
| 모듈 | 기능 |
|---|---|
| `macro_sector_strength.py` | 섹터 강도 점수 (detrended YoY% z-score, 인플레 bias 제거) |
| `macro_cycle.py` | K-means 4-cluster (회복/확장/둔화/침체) — **현재 "확장" 국면** |
| `alerts.py` | 매트릭스 cells \|z\| ≥ 임계치 자동 알림 (현재 58 cells extreme) |
| `sector_leadlag.py` | 페어별 cross-correlation (CCF) + 섹터별 net lead score |
| `events.py` | 한은 금통위 / Fed FOMC / 실적시즌 캘린더 |

### 2.2 종목 / 전략
| 모듈 | 기능 |
|---|---|
| `leader_stock.py` | 종목 점수 = 0.30 RS + 0.20 TT + 0.15 VCP + 0.10 EPS + 0.25 macro |
| `stock_matrix.py` | 4,516 KRX × 10 metric 매트릭스 (가격 RS·TT·VCP·거래대금·외인·EPS·ROE·PER·PBR·sector_macro) |
| `pead.py` | DART YoY EPS + 매크로 강세 결합 (PEAD 후보) |
| `flow_macro.py` | 외인 순매수 × 매크로 cells cross-correlation |
| `alpha_library.py` | 사용자 정의 strategy DSL + 3개 preset (가치/모멘텀/펀더+매크로) |

### 2.3 백테스트 / 예측
| 모듈 | 기능 |
|---|---|
| `sector_rotation_backtest.py` | TOP-N long / BOTTOM-N short, 월별 리밸런싱, 거래비용 반영 |
| `leverage_strategy.py` | KODEX 레버리지/인버스 회전 (z 임계치 트리거) |
| `ml_forecast.py` | OLS baseline (sector ETF next-quarter 예측, walk-forward 향후) |
| `global_matrix.py` | WICS ↔ GICS 11 매핑 (US 1차 → 글로벌 cross-country lead-lag) |

### 2.4 운영 / 시각화
| 모듈 | 기능 |
|---|---|
| `briefing.py` | 일일 브리핑 자동 생성 (rule-based markdown + LLM 옵션, 10분 캐시) |
| `llm_ask.py` | 자연어 질의 (Anthropic / OpenAI / rule-based fallback, prompt caching) |
| `paper_trade.py` | EOD 신호 → 가상 매매 기록 (positions/orders DB) |
| `quality_monitor.py` | 매일 셀 단위 verifier 결과 audit DB 저장 + 회귀 alert |

---

## 3. API (FastAPI) — 24개 endpoint

### Phase 1 (11개)
```
GET  /api/v1/research/sector-indicators/sectors        WICS 34 메타
GET  /api/v1/research/sector-indicators/matrix         340 cells × {latest, 12M%, 3Y_z, sparkline}
GET  /api/v1/research/sector-indicators/series/{id}    단일 cell 30년 시계열 (period 토글)
GET  /api/v1/research/sector-indicators/symbols        sector × KRX 종목
GET  /api/v1/research/sector-indicators/strength       섹터 강도 ranking (detrended/raw)
GET  /api/v1/research/sector-indicators/leaders        주도주 후보 TOP-N
GET  /api/v1/research/sector-indicators/leadlag        페어별 lead-lag + sector lead score
GET  /api/v1/research/sector-indicators/backtest       섹터 로테이션 백테스트 결과
GET  /api/v1/research/sector-indicators/cycle          매크로 사이클 클러스터링
GET  /api/v1/research/sector-indicators/alerts         |z| ≥ threshold cells
GET  /api/v1/research/sector-indicators/briefing       일일 브리핑 마크다운
```

### Phase 2 (13개 신규)
```
GET  /quality/snapshot       데이터 품질 + ky.db audit
GET  /quality/history        품질 이력 (regression detection)
GET  /stocks/matrix          종목 매트릭스 (4516 × 10)
GET  /ask                    LLM 자연어 질의
GET  /flow_macro             외인 수급 × 매크로
GET  /calendar               이벤트 캘린더
GET  /pead                   PEAD 후보
GET  /paper/positions        paper 포지션 + P&L
GET  /paper/signal           EOD 신호 → paper 액션
POST /strategy/run           사용자 DSL 실행
GET  /strategy/presets       3개 preset (가치/모멘텀/펀더+매크로)
GET  /leverage/backtest      KODEX 레버리지/인버스 백테스트
GET  /global/mapping         WICS ↔ GICS 매핑
GET  /forecast               ML next-quarter 예측
```

---

## 4. Frontend (Next.js 14)

### `/research/sector-indicators` — 5 panel 통합 화면
```
┌─ 헤더 (충실도 % + As-of) ─────────────────────────────────┐
├─ 섹터 강도 랭킹 (TOP 5 / BOTTOM 5, YoY%/Raw 토글) ────────┤
├─ 백테스트 패널 (3 전략 × NAV 스파크라인 + CAGR/Sharpe/MDD)─┤
├─ 주도주 TOP 20 (티커·이름·섹터·leader_score·macro_z·사유)─┤
├─ 매트릭스 그리드 (34 × 10, 6단계 색상 z-score, 셀 클릭)──┤
└─ 셀 drawer (시계열 차트 + 1Y/3Y/10Y/All 토글 + 종목리스트)┘
```

### 추가 페이지 (Phase 4)
- `/research/briefing/today` — 일일 브리핑
- `/strategies` — strategy editor (DSL UI)
- `/stocks/{ticker}` — 종목 상세 (가격 RS · DART · 섹터 매크로 노출도)
- `/cycle` — 매크로 사이클 도식
- `/calendar` — 이벤트 + 직전 영향 베이스라인
- `/portfolio/paper` — 가상 포지션 P&L
- `/quality` — 데이터 품질 이력

---

## 5. 운영 인프라

### 5.1 PID-aware dev server (`scripts/server.sh`)
```bash
bash scripts/server.sh start     # api 8300 + web 8380, PID 추적
bash scripts/server.sh stop      # 정확히 ky 프로세스만 종료
bash scripts/server.sh status    # port-aware 헬스 체크
bash scripts/server.sh logs api  # uvicorn log tail
```

### 5.2 EOD 자동화 (`scripts/eod_collect.sh`)
```bash
bash scripts/eod_collect.sh fast              # 4종 출처, 7분
bash scripts/eod_collect.sh full              # 14종 전체, 30~60분
bash scripts/eod_collect.sh sources fred kosis  # 특정 출처만

# Windows Task Scheduler (KST 17:00 매일):
powershell -File scripts/eod_register_task.ps1
```

### 5.3 Cell-level verifier (`scripts/verify_cells_v4.py`)
- sector-tag + source-folder + DART corp ABBR 매칭
- 출력: 미달 cells + per-source coverage

### 5.4 데이터셋 export (`scripts/export_dataset.py`)
- 1.2GB raw → CSV/Parquet zip + datasheet (학술/공유용)

---

## 6. 백테스트 결과 (검증된 alpha)

### 섹터 로테이션 TOP3 long-only (2015~2026, 11년)
| Metric | 결과 |
|---|---|
| **CAGR** | **+13.6%** |
| Sharpe | 0.58 |
| MDD | -43.2% |
| Win rate | 53.7% |
| **vs EW 벤치** | **+1.7%/y alpha** |

### 비교
- TOP3-BOT3 long-short: 0.0% (BOTTOM이 시장에서 underperform 안 함)
- TOP5 long-only: TOP3보다 낮음 (집중도 ↓ → alpha 희석)

→ **TOP3 long-only가 sweet spot**. 거래비용 (0.18% 매도세 + 0.015% 수수료 + 0.1% 슬리피지) 모두 반영.

---

## 7. Cell-level 검증

```
Total cells:    340  (WICS 34 × 10)
Filled:         334  =  98.2%
Underbacked:      6  (2.1%)  ← 안정 무료 API 부재 (본질적 ceiling)
```

**남은 6 cells**: 인천공항 항공여객 / IDC 스마트폰 / Statista 가전 / 방사청 수출 / BHKP 펄프 / Bloomberg 전자부품 / Ericsson 5G — 모두 무료 API 없음.

---

## 8. 라이센스 / 비즈니스 모델

| Tier | 어디 | 가격 |
|---|---|---|
| **OSS** | 이 repo (Apache 2.0) | 무료, BYO API key, self-host |
| **Cloud** | gazua.app (예정) | 월 구독, hosted + 카톡/Slack 자동 알림 |
| **API** | gazua.app/api (예정) | 요청량 별 plan |
| **Enterprise** | 별도 | 글로벌 매트릭스, ML 모델, custom DSL |

상세는 `docs/ROADMAP.md` §10.

---

## 9. Quick Start (Phase 1~5 추출 완료 후)

```bash
git clone https://github.com/yuyongkim/GAZUA
cd GAZUA && cp .env.example .env  # API 키 채우기 (FRED, ECOS, KOSIS 등)
docker compose up -d              # web + api + sqlite

# 데이터 수집 (첫 실행 30~60분)
docker exec gazua-api python scripts/collect_sectors.py --source fred
docker exec gazua-api python scripts/collect_sectors.py  # 전체

# 매트릭스 페이지
open http://localhost:8380/research/sector-indicators
```

---

## 10. 운영 원칙 (한강뷰 5계명)

1. **데이터로 결정** — 감/뉴스/카톡 단톡방 아닌 매트릭스
2. **분산** — TOP 3 섹터 ETF, 종목당 자본 5% 이하
3. **손절 우선** — -7% 도달 시 무조건 (Minervini)
4. **사이클 인지** — 침체기엔 노출 ↓ (KODEX 인버스 활용 옵션)
5. **백테스트 위에서만 약속** — 라이브 트랙 없는 전략에 자본 투입 금지

---

## 11. Status / Roadmap

- 📦 Phase 0 — Bootstrap ✅ (이 commit)
- 🚧 Phase 1 — Core 추출 (scoring/ 18개 모듈) — 진행 예정
- 🚧 Phase 2 — Adapter 추출 (18 무료 source)
- 🚧 Phase 3 — API 추출 (24 endpoints)
- 🚧 Phase 4 — Web 추출 (5 panel)
- 🚧 Phase 5 — Self-host 가이드 + Docker
- 🚧 Phase 6 — Community + 첫 release v0.1.0

상세: [docs/ROADMAP.md](docs/ROADMAP.md)

---

## 12. Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) 참조. 백테스트 무결성 + 거래비용 반영 + 데이터 출처 표기 — 절대 원칙.

위트 카피는 페이지 만들 때 그 자리 어울리는 걸로. 한 번에 카피 풀 만들지 않음.

---

## 13. License

- 코어 (`apps/`, `packages/`, `docs/`): **Apache 2.0** (이 repo)
- Premium SaaS 영역 (`gazua-cloud`, private): **BSL 1.1 → Apache 2.0 (3년 후)**

---

> _가즈아는 야성, 데이터는 사다리. 야수가 사다리를 들면 한강뷰._

**Maintainer**: [@yuyongkim](https://github.com/yuyongkim)
**Issues / Discussions**: [GitHub](https://github.com/yuyongkim/GAZUA)
