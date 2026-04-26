"""Paper trading — EOD signal → ky.db에 모의 포지션 기록 + P&L 추적.

흐름:
  1. EOD 후 매크로 strength TOP-N 섹터 ETF 선정
  2. paper_orders에 매수 기록 (가상 자본 1억원 / 종목당 균등 배분)
  3. 다음 EOD 때 close 가격으로 평가 → paper_positions의 P&L 갱신
  4. 월 1회 리밸런싱 — 새 strength TOP-N 으로 교체

ky.db에 두 테이블 신설:
  paper_orders   — 모든 매매 기록
  paper_positions — 현재 보유 + P&L
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"

INITIAL_CAPITAL = 100_000_000  # 1억 원
COST_RATE = 0.00015 + 0.001    # 수수료 + 슬리피지
TAX_RATE = 0.0018              # 매도세


@dataclass
class PaperOrder:
    id: int | None
    ticker: str
    side: str       # 'BUY' / 'SELL'
    qty: int
    price: float
    cost_krw: float
    timestamp: str
    note: str = ""


@dataclass
class PaperPosition:
    ticker: str
    name: str
    qty: int
    avg_cost: float
    cur_price: float | None
    market_value: float
    pnl_krw: float
    pnl_pct: float


def _ensure_tables(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price REAL NOT NULL,
            cost_krw REAL NOT NULL,
            timestamp TEXT NOT NULL,
            note TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            ticker TEXT PRIMARY KEY,
            qty INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            opened_at TEXT NOT NULL,
            note TEXT
        )
    """)


def place_order(ticker: str, side: str, qty: int, price: float, note: str = "") -> int:
    """모의 주문 기록 + paper_positions 업데이트."""
    if qty <= 0 or price <= 0:
        raise ValueError("qty/price must be positive")
    con = sqlite3.connect(DB_PATH)
    try:
        _ensure_tables(con)
        gross = qty * price
        if side == "BUY":
            cost_krw = gross * (1 + COST_RATE)
        else:
            cost_krw = -gross * (1 - COST_RATE - TAX_RATE)  # net inflow on sell
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        cur = con.execute(
            "INSERT INTO paper_orders (ticker, side, qty, price, cost_krw, timestamp, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (ticker, side, qty, price, cost_krw, ts, note),
        )
        oid = cur.lastrowid

        # Update positions
        cur_row = con.execute(
            "SELECT qty, avg_cost FROM paper_positions WHERE ticker = ?", (ticker,)
        ).fetchone()
        if side == "BUY":
            if cur_row:
                old_qty, old_avg = cur_row
                new_qty = old_qty + qty
                new_avg = (old_avg * old_qty + price * qty) / new_qty
                con.execute(
                    "UPDATE paper_positions SET qty=?, avg_cost=? WHERE ticker=?",
                    (new_qty, new_avg, ticker),
                )
            else:
                con.execute(
                    "INSERT INTO paper_positions (ticker, qty, avg_cost, opened_at, note) "
                    "VALUES (?,?,?,?,?)",
                    (ticker, qty, price, ts, note),
                )
        else:  # SELL
            if cur_row:
                old_qty, _ = cur_row
                new_qty = old_qty - qty
                if new_qty <= 0:
                    con.execute("DELETE FROM paper_positions WHERE ticker=?", (ticker,))
                else:
                    con.execute(
                        "UPDATE paper_positions SET qty=? WHERE ticker=?",
                        (new_qty, ticker),
                    )
        con.commit()
        return oid
    finally:
        con.close()


def list_positions() -> list[PaperPosition]:
    """현재 paper_positions + 시가평가."""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        _ensure_tables(con)
        rows = con.execute(
            "SELECT p.ticker, p.qty, p.avg_cost, u.name "
            "FROM paper_positions p LEFT JOIN universe u ON p.ticker = u.ticker"
        ).fetchall()
        positions: list[PaperPosition] = []
        for r in rows:
            cur_price = _last_close(r["ticker"])
            mv = (cur_price or r["avg_cost"]) * r["qty"]
            cost = r["avg_cost"] * r["qty"]
            pnl = mv - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
            positions.append(PaperPosition(
                ticker=r["ticker"], name=r["name"] or r["ticker"],
                qty=r["qty"], avg_cost=round(r["avg_cost"], 2),
                cur_price=cur_price, market_value=round(mv),
                pnl_krw=round(pnl), pnl_pct=round(pnl_pct, 2),
            ))
        return positions
    finally:
        con.close()


def _last_close(ticker: str) -> float | None:
    """ohlcv 테이블 last close. 없으면 None."""
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(DB_PATH)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(ohlcv)").fetchall()}
        if "ticker" not in cols and "symbol" not in cols:
            return None
        ticker_col = "ticker" if "ticker" in cols else "symbol"
        date_col = "date" if "date" in cols else "trade_date"
        close_col = "close" if "close" in cols else "close_price"
        sql = f"SELECT {close_col} FROM ohlcv WHERE {ticker_col} = ? ORDER BY {date_col} DESC LIMIT 1"
        row = con.execute(sql, (ticker,)).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        con.close()


def daily_signal_to_orders(top_n: int = 3, dry_run: bool = True) -> dict[str, Any]:
    """EOD 신호 → orders 자동 생성 (단계 A: 종이 거래만, 실거래 토글 OFF)."""
    from ky_core.scoring.macro_sector_strength import compute_macro_strength
    from ky_core.scoring.sector_rotation_backtest import SECTOR_ETF_SLUG

    strengths = compute_macro_strength(method="detrended")
    eligible = [(s.sector_num, s.score) for s in strengths if s.sector_num in SECTOR_ETF_SLUG]
    eligible.sort(key=lambda x: x[1], reverse=True)
    top = eligible[:top_n]

    actions = []
    for sec_num, score in top:
        folder, slug = SECTOR_ETF_SLUG[sec_num]
        # ETF symbol from slug (e.g. "semiconductor__KODEX_반도체_(091160.KS)" → 091160)
        import re
        m = re.search(r"\((\d{6})\.[A-Z]+\)", slug)
        if not m:
            continue
        etf_ticker = m.group(1)
        actions.append({
            "sector_num": sec_num,
            "etf_ticker": etf_ticker,
            "etf_slug": slug,
            "score": round(score, 3),
            "decision": "BUY equal-weighted" if not dry_run else "DRY-RUN BUY",
        })
    return {
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "strategy": f"sector_rotation_top{top_n}",
        "dry_run": dry_run,
        "actions": actions,
        "n_actions": len(actions),
    }
