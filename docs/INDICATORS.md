# 매트릭스 정의 (340 cells)

> WICS 중분류 34섹터 × 10지표 = **340 cells**.
> 30년 시계열 (1995~2026, 출처별 깊이 차등).

---

## 셀 출처 분포 (340 cells)

| 출처 | 셀 수 | 비고 |
|---|---|---|
| KIS 업종지수 | 34 (모든 섹터 #10) | 한국 sector 가격 (yf_commodities ETF로 보강) |
| KOSIS | 81 | 산업생산·출하·재고·인구·고용 |
| Crawl⚠ | 63 | 안정 API 없는 cells (yf ETF로 부분 대체) |
| ECOS | 55 | 한국 매크로 (금리·환율·BSI·CSI) |
| DART | 34 | 71 corps × 26년 사업보고서 |
| FRED | 31 | US 매크로 |
| TRASS (관세청) | 17 | HS-level 수출입 |
| pytrends | 16 | 검색관심도 |
| EIA | 10 | 에너지 |
| WB | 8 | 글로벌 GDP |
| OECD | 6 | CLI 5개국 |
| CFTC | 5 | COT |
| yfinance | 2+23 ETF | 글로벌 commodity + 한국 sector ETF |
| Comtrade | 1 | 글로벌 cross-check |
| IMF | 1 | (yfinance 대체) |

---

## 섹터별 정의 (WICS 34)

| # | sector_num | slug | label | universe.sector 매핑 | 대표 ETF |
|---|---|---|---|---|---|
| 01 | energy | 에너지 | "에너지", "석유/가스" | KODEX 에너지화학 (117460) |
| 02 | chemical | 화학 | "화학" | KODEX 화학 (091230) |
| 03 | nonferrous | 비철금속 | "금속", "비금속" | TIGER 비철금속 (139250) |
| 04 | steel | 철강 | "금속", "철강" | KODEX 철강 (117680) |
| 05 | construction | 건설 | "건설", "건설 및 엔지니어링" | KODEX 건설 (117700) |
| 06 | machinery | 기계 | "일반기계", "기계 및 장비" | (없음 — yf 대체) |
| 07 | ship | 조선 | "조선/기계" | KODEX 조선 (102960) |
| 08 | trade | 상사·자본재 | "상사/자본재", "기타금융" | (없음) |
| 09 | transport | 운송 | "운송/창고" | SEA (글로벌) |
| 10 | auto | 자동차·부품 | "자동차", "자동차 및 부품" | KODEX 자동차 (091180) |
| 11 | cosmetic | 화장품·의류 | "섬유/의류" | TIGER 화장품 (228790) |
| 12 | tourism | 호텔·레저 | "서비스/레저" | (없음) |
| 13 | media | 미디어·교육 | "미디어/엔터", "교육서비스" | TIGER K-게임 (300610) |
| 14 | retail | 유통 | "유통/소매", "도매업" | KODEX 유통 (091170) |
| 15 | staple | 필수소비재 | "식료품/생필품" | (없음) |
| 16 | food | 식품·담배 | "음식료품", "담배" | KODEX 음식료 (140710) |
| 17 | pharma | 제약·바이오 | "제약 및 바이오" | KODEX 헬스케어 (266420) |
| 18 | bank | 은행 | "은행" | (없음) |
| 19 | broker | 증권 | "증권" | KODEX 증권 (102970) |
| 20 | insurance | 보험 | "보험" | KODEX 보험 (140700) |
| 21 | fincap | 다각화 금융 | "기타금융", "금융" | (없음) |
| 22 | software | 소프트웨어 | "IT 서비스 및 컨설팅" | TIGER 200 IT (139260) |
| 23 | itservice | IT 서비스 | "IT 서비스 및 컨설팅" | KODEX IT (266370) |
| 24 | display | 디스플레이 | "디스플레이" | TIGER 디스플레이 (139220) |
| 25 | handset | 핸드셋 | "전기/전자", "통신장비" | (없음) |
| 26 | semiconductor | 반도체 | "반도체", "반도체 및 관련장비" | KODEX 반도체 (091160) |
| 27 | itappliance | IT 가전 | "전기/전자", "가전제품" | (없음) |
| 28 | electric | 전기장비 | "전기/전자" | (없음) |
| 29 | telecom | 통신 서비스 | "통신서비스" | TIGER 200 통신 (139310) |
| 30 | utility | 유틸리티 | "전기/가스/수도" | (없음) |
| 31 | realestate | 부동산 | "부동산", "부동산 (REITs)" | TIGER 부동산 (157500) |
| 32 | defense | 우주항공·방위 | "방산", "운수장비" | ARIRANG 우주항공 (449290) |
| 33 | paper | 종이·목재 | "종이/목재" | (없음) |
| 34 | electronics | 일반 전기전자 | "전기/전자" | (없음) |

---

## 지표 카테고리

각 섹터당 10 지표 = **실물 + 매크로 + 가격 + 펀더 + 심리** 5축 균형:
- **실물** (생산·출하·재고·수출입): 평균 4-5/sector
- **매크로** (금리·환율·PMI·CLI): 평균 1-2
- **가격** (원자재·KIS index): 평균 1-2
- **펀더** (DART 분기실적): 1
- **심리** (BSI/CSI/pytrends): 0-1

선/동/후행 분포:
- **선행**: ~150 cells (생산능력·신규수주·기준금리·CLI·BSI·검색관심도)
- **동행**: ~140 cells (생산지수·소매판매·환율)
- **후행**: ~50 cells (DART 실적·재고)

---

## 셀 형식 (CSV schema)

저장: `~/.gazua/data/sectors/<source>/<sector>__<indicator>.csv`

```
source_id, series_id, date, value, unit
fred,      DGS10,     2026-04-23, 4.34, Percent
```

기본 column: `date` + `value`. 출처별 추가 컬럼은 `meta` JSON.

---

## 매트릭스 변경 절차

1. `packages/core/sectors/registry.py` 의 `SECTOR_INDICATOR_MAP` 수정
2. `python scripts/verify_cells_v4.py` — 새 cell 매핑 확인
3. `python scripts/collect_sectors.py --source <new>` — 데이터 수집
4. PR 시 datasheet 갱신 + license 명시

---

자세한 cell × indicator 매핑 (수치 포함)은 `python scripts/sector_coverage.py` 출력
또는 `/api/v1/research/sector-indicators/matrix` API 응답 참조.
