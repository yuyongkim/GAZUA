# GAZUA

> **가즈아에 데이터를 얹다.**

Retail 투자자의 가즈아! 외침 위에, 30년 매크로 매트릭스·섹터 강도·백테스트를
얹어주는 데이터 도구.

감정과 야성을 부정하지 않습니다. 단지 **결정 직전에 데이터 한 장**을 보여줍니다.

---

## What

- WICS 34섹터 × 10지표 × 30년 매트릭스 (FRED·ECOS·KOSIS·관세청·DART·OECD·...)
- 매트릭스 → 섹터 강도 점수 (z-score) → 매주 강세 섹터 TOP-N
- 가격 RS · TT · VCP · 펀더 + 섹터 매크로 결합 종목 점수
- 섹터 로테이션 백테스트 (30년 IS/OOS)
- 자연어 질의 (LLM context: 매트릭스)
- 매일 자동 EOD 수집 + 일일 브리핑

## Why

| 한강물 | 한강뷰 |
|---|---|
| FOMO · 감 · 뉴스 | 매트릭스 · z-score · 30년 백테스트 |
| 진입가 후회 | 진입 사유 적힌 시그널 |
| 손절 못함 | 룰 기반 -7% 자동 alert |

## Status

🚧 **Open Core 초기 단계.** ky-platform (사내 풀스택)에서 점진적으로
공개 가능한 부분을 추출 중.

- `apps/web`        — Next.js 14 dashboard (pending)
- `apps/api`        — FastAPI (pending)
- `packages/core`   — scoring · backtest · 매트릭스 정의 (pending)
- `packages/adapters` — 무료 데이터 소스 어댑터 (pending)

자세한 추출 계획은 [docs/ROADMAP.md](docs/ROADMAP.md).

## License

- 코어 (`apps/`, `packages/`, `docs/`): **Apache 2.0**
- Premium SaaS 영역 (별도 repo `gazua-cloud`): BSL 1.1 → Apache 2.0 (3년 후)

OSS 자체 호스팅은 무료, hosted 버전은 [gazua.app](https://gazua.app) (예정).

## Quick Links

- 매트릭스 개념: [docs/INDICATORS.md](docs/INDICATORS.md) (예정)
- 아키텍처: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (예정)
- 로드맵: [docs/ROADMAP.md](docs/ROADMAP.md) (예정)

## Contributing

초기 단계입니다. issue / PR 환영. CONTRIBUTING.md 작성 예정.

---

_가즈아는 야성, 데이터는 사다리. 야수가 사다리를 들면 한강뷰._
