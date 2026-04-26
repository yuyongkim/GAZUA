# Contributing to GAZUA

> 가즈아에 데이터를 얹다. 그 데이터의 정확성과 재현성이 핵심.

## 시작하기

1. Fork → clone → branch
2. Issue 먼저 (특히 큰 변경)
3. PR 보낼 때 명확한 commit message + reproducible test

## 절대 원칙

1. **백테스트 무결성** — look-ahead bias 금지. 모든 신호는 t 시점 정보로만.
2. **거래비용 반영** — 매트릭스 전략 백테스트는 항상 0.18% 세금 + 수수료 + 슬리피지 0.1% 포함.
3. **데이터 출처 표기** — 새 어댑터 추가 시 license + ToS 확인 필수.
4. **위트 카피는 페이지 만들 때** — 한 번에 카피 풀 만들지 않음. PR로 카피만 추가는 reject.

## 환영하는 PR

- 새 무료 데이터 어댑터 (라이센스·rate-limit 명시 필수)
- 매트릭스 셀 정의 보강 (sector × indicator)
- 백테스트 변종 (시즌별, 변동성 가중 등)
- Self-host 가이드 개선
- 한국어/영어 docs 번역

## 환영하지 않는 PR

- broker API 자동 주문 (실거래) — 보안·법적 리스크
- 유료 데이터 source proxy (license 위반)
- 카피만 변경하는 PR (이슈에서 토론 후 페이지 작업과 함께)
- 위트 톤 외 (자기비하·과장 광고·공포 마케팅)

## 톤 가이드 (페이지 카피)

- 야성 인정하되, 데이터로 사다리 제공
- 한강물 vs 한강뷰 메타포 일관성
- 자기비하 X, 과장 X, FUD X
- 한국 retail 정서 + 데이터 기반 합리성

## 라이센스

- 코어 코드: Apache 2.0
- Premium 영역: BSL 1.1 (별도 repo `gazua-cloud`)
- 데이터: 각 source 라이센스 따름 (FRED/ECOS public domain, customs/KOSIS open data 등)

PR 보내실 때 자동으로 위 라이센스에 동의하는 것으로 간주합니다.
