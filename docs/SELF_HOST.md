# Self-host Guide

> "한강뷰는 셀프 호스팅도 가능합니다."

## 5분 시작 (Docker)

```bash
# 1. clone + env
git clone https://github.com/yuyongkim/GAZUA
cd GAZUA
cp .env.example .env
# .env에 적어도 FRED / ECOS / KOSIS API key 채우기 (모두 무료)

# 2. compose up
docker compose up -d

# 3. 첫 데이터 수집 (10~15분, fast 모드)
docker exec gazua-api bash scripts/eod_collect.sh fast

# 4. 매트릭스 페이지 열기
open http://localhost:8380/research/sector-indicators
```

## 로컬 개발 (Docker 없이)

### Backend
```bash
cd apps/api
pip install -e ../../packages/core ../../packages/adapters
pip install fastapi uvicorn[standard] pydantic-settings python-dotenv sqlalchemy httpx requests pandas openpyxl yfinance pytrends
uvicorn main:app --reload --port 8300
```

### Frontend
```bash
cd apps/web
npm install
npm run dev -- -p 8380
```

또는 헬퍼 스크립트:
```bash
bash scripts/server.sh start    # api + web 둘 다 백그라운드
bash scripts/server.sh status   # PID + URL 확인
bash scripts/server.sh stop     # 정확히 GAZUA만 종료
```

## 첫 실행 후 확인

```bash
# 셀 단위 검증
python scripts/verify_cells_v4.py

# coverage 요약
python scripts/sector_coverage.py

# 일일 브리핑 직접 호출
curl http://localhost:8300/api/v1/research/sector-indicators/briefing
```

## EOD 자동화

### Linux/macOS (cron)
```bash
0 17 * * 1-5  /path/to/GAZUA/scripts/eod_collect.sh full >> /tmp/gazua-eod.log 2>&1
```

### Windows (Task Scheduler)
```powershell
powershell -File scripts/eod_register_task.ps1
```

매일 KST 17:00 실행 + 결과 `runtime_logs/eod_YYYYMMDD.log`.

## 데이터 위치

```
~/.gazua/
├ shared.env             ← API keys (절대 commit 금지)
└ data/
   ├ sectors/            ← 1.2GB raw CSV (수집 후)
   │   ├ fred/
   │   ├ ecos/
   │   ├ customs/
   │   ├ kosis/
   │   ├ dart/
   │   └ ...
   └ gazua.db            ← sqlite (universe + audit + paper trade)
```

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| API 200 OK인데 매트릭스 비어있음 | `scripts/eod_collect.sh fast` 한 번 실행 후 5분 캐시 invalidate |
| DART connection reset | API rate-limit. 1시간 후 재시도. |
| pytrends 403 / sorry/index | Google 차단. 30분~1시간 대기. `--source-sleep 12` 추가. |
| KOSIS err=21 | tblId 없음. `docs/INDICATORS.md` 의 검증된 tblId만 사용. |
| KIS 0 rows | broker key 없음. `.env`에 KIS_APP_KEY/KIS_APP_SECRET 채우거나, yfinance ETF 사용. |

## 시간 깊이 확인

수집 후 다음 명령으로 확인:
```bash
python scripts/verify_cells_v4.py
# → Total cells: 340 / Filled: 334 = 98.2%

python scripts/sector_coverage.py
# → 출처별 fill 상태
```

## License

- Self-host: 무료, BYO API key
- Hosted (gazua.app, 예정): 구독, 카톡/Slack 알림 포함

상세는 [README.md](../README.md) §8.
