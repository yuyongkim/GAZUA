"""BacktestEngine — real-data point-in-time simulator.

Design
------
1. One bulk load of ``ohlcv`` for the entire requested date range
   and universe (KOSPI+KOSDAQ, non-ETF). ~10 M rows → ~8 s, cached.
2. Daily loop. For each trading day ``d``:
     - mark-to-market open positions against ``d``'s close;
     - hit stops / targets on ``d``'s low/high (conservative);
     - if today is a rebalance day, the strategy produces a ranked
       pick list using data **up to ``d``'s close**; orders are
       filled at ``d+1``'s open (next-open rule).
3. All trade costs come from :class:`~ky_core.backtest.cost.CostModel`.
4. Output is a single JSON artefact in
   ``~/.gazua/data/backtest/run_<id>.json``.

Strategy protocol
-----------------
A strategy is an object with:
    * ``name: str``
    * ``rebalance: str`` — 'weekly' | 'monthly' | 'quarterly' | 'annual'
    * ``def pick(view: PanelView, as_of: str) -> list[Candidate]``

``Candidate`` is a light dataclass the engine uses to open positions.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text

from ky_core.backtest.cost import CostModel, DEFAULT_COST
from ky_core.backtest.metrics import Metrics, compute_metrics
from ky_core.backtest.portfolio import Portfolio, Position, Trade
from ky_core.backtest._helpers import (
    new_run_id as _new_run_id,
    pick_rebalance_dates as _pick_rebalance_dates,
    equal_weight_index as _equal_weight_index,
    sector_attribution as _sector_attribution,
)
from ky_core.storage.db import get_engine, init_db

logger = logging.getLogger(__name__)

DEFAULT_DB_OUT = Path.home() / ".gazua" / "data" / "backtest"
DEFAULT_MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ")


# --------------------------------------------------------------------------- #
# Candidate + Strategy protocol                                               #
# --------------------------------------------------------------------------- #


@dataclass
class Candidate:
    symbol: str
    score: float                   # higher = better
    reason: str = ""
    target_mult: float = 2.0       # target = entry * (1 + stop_pct*target_mult)


class Strategy(Protocol):
    name: str
    rebalance: str

    def pick(self, view: "PanelView", as_of: str) -> list[Candidate]: ...


# --------------------------------------------------------------------------- #
# PanelView — point-in-time accessor over the wide panel                      #
# --------------------------------------------------------------------------- #


@dataclass
class PanelView:
    """Read-only slice of the panel valid only up to ``as_of``.

    Strategies receive this and MUST NOT peek past ``as_of``. Helpers
    use bisect on the (pre-sorted) row lists to stay O(log n) per
    lookup.
    """
    _series: dict[str, list[dict[str, Any]]]
    _universe: dict[str, dict[str, Any]]
    _cutoff: str  # inclusive

    @property
    def as_of(self) -> str:
        return self._cutoff

    @property
    def universe(self) -> dict[str, dict[str, Any]]:
        return self._universe

    def _cut_index(self, symbol: str) -> int:
        """Return `i` such that series[0:i] are rows with date <= cutoff."""
        from bisect import bisect_right
        rows = self._series.get(symbol) or []
        # build list of dates lazily on the fly -- cheap enough given bisect
        dates = [r["date"] for r in rows]
        return bisect_right(dates, self._cutoff)

    def closes_up_to(self, symbol: str, *, n: int | None = None) -> list[float]:
        rows = self._series.get(symbol) or []
        i = self._cut_index(symbol)
        cut = rows[max(0, i - n):i] if n is not None else rows[:i]
        return [r["close"] for r in cut if r["close"] is not None]

    def rows_up_to(self, symbol: str, *, n: int | None = None) -> list[dict[str, Any]]:
        rows = self._series.get(symbol) or []
        i = self._cut_index(symbol)
        return rows[max(0, i - n):i] if n is not None else rows[:i]

    def available_symbols(self, *, min_history_days: int = 30) -> list[str]:
        out: list[str] = []
        for sym in self._series:
            if self._cut_index(sym) >= min_history_days:
                out.append(sym)
        return out


# --------------------------------------------------------------------------- #
# Configuration + run artefact                                                #
# --------------------------------------------------------------------------- #


@dataclass
class BacktestConfig:
    strategy_name: str
    start: str               # ISO "YYYY-MM-DD"
    end: str
    initial_cash: float = 100_000_000.0   # 100M KRW
    markets: tuple[str, ...] = DEFAULT_MARKETS
    max_positions: int = 10
    max_per_sector: int = 3
    risk_per_trade_pct: float = 0.02
    stop_loss_pct: float = 0.07
    benchmark_symbol: str | None = "KOSPI"  # synthetic: equal-weight KOSPI EW index
    cost: CostModel = field(default_factory=lambda: DEFAULT_COST)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cost"] = self.cost.to_dict()
        d["markets"] = list(self.markets)
        return d


@dataclass
class BacktestRun:
    run_id: str
    config: BacktestConfig
    equity_curve: list[dict[str, Any]]
    benchmark_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    metrics: Metrics
    sector_attribution: dict[str, dict[str, Any]]
    universe_size: int
    n_trading_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config": self.config.to_dict(),
            "equity_curve": self.equity_curve,
            "benchmark_curve": self.benchmark_curve,
            "trades": self.trades,
            "metrics": self.metrics.to_dict(),
            "sector_attribution": self.sector_attribution,
            "universe_size": self.universe_size,
            "n_trading_days": self.n_trading_days,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }


# --------------------------------------------------------------------------- #
# Engine                                                                      #
# --------------------------------------------------------------------------- #


class BacktestEngine:
    """Run a strategy over the ky.db historical panel.

    Usage::

        engine = BacktestEngine(config)
        run = engine.run(strategy)
        path = engine.save(run)
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self._series: dict[str, list[dict[str, Any]]] | None = None
        self._universe: dict[str, dict[str, Any]] | None = None
        self._trading_days: list[str] | None = None
        # date -> {symbol -> bar}; populated post-load for O(1) bar access
        self._by_date: dict[str, dict[str, dict[str, Any]]] | None = None

    # ---------- loading ------------------------------------------------

    def _load_panel(self) -> None:
        if self._series is not None:
            return
        init_db()
        engine = get_engine()
        # SEPA needs 200+ trading days of history; load 400 cal-days before start.
        pre_start = (datetime.fromisoformat(self.config.start) - timedelta(days=420)).date().isoformat()
        placeholders = ",".join(f":m{i}" for i in range(len(self.config.markets)))
        params: dict[str, Any] = {f"m{i}": m for i, m in enumerate(self.config.markets)}
        params.update({"start": pre_start, "end": self.config.end})
        logger.info("Loading OHLCV panel: %s → %s", pre_start, self.config.end)
        t0 = time.time()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT symbol, date, open, high, low, close, volume
                    FROM ohlcv
                    WHERE owner_id = 'self'
                      AND market IN ({placeholders})
                      AND date BETWEEN :start AND :end
                    ORDER BY symbol, date
                    """
                ),
                params,
            ).fetchall()
        series: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            series.setdefault(r.symbol, []).append(
                {"date": r.date, "open": r.open, "high": r.high,
                 "low": r.low, "close": r.close, "volume": r.volume}
            )
        # Universe
        uni: dict[str, dict[str, Any]] = {}
        with engine.connect() as conn:
            u = conn.execute(
                text(
                    f"""
                    SELECT ticker, market, name, sector
                    FROM universe
                    WHERE owner_id = 'self'
                      AND market IN ({placeholders})
                      AND COALESCE(is_etf, 0) = 0
                    """
                ),
                {f"m{i}": m for i, m in enumerate(self.config.markets)},
            ).fetchall()
        for r in u:
            uni[r.ticker] = {
                "symbol": r.ticker, "market": r.market,
                "name": r.name or r.ticker, "sector": r.sector or "기타",
            }
        # Drop symbols without universe meta (keeps us honest)
        series = {s: rs for s, rs in series.items() if s in uni and len(rs) >= 60}
        self._series = series
        self._universe = {s: uni[s] for s in series}
        # Build the global list of trading days from what we actually have
        days = sorted({r["date"] for rs in series.values() for r in rs})
        self._trading_days = [d for d in days if self.config.start <= d <= self.config.end]
        # Reverse index: date -> {symbol -> bar} for O(1) per-day lookup
        by_date: dict[str, dict[str, dict[str, Any]]] = {}
        for sym, rows in series.items():
            for r in rows:
                by_date.setdefault(r["date"], {})[sym] = r
        self._by_date = by_date
        dt = time.time() - t0
        logger.info(
            "Panel loaded: %d symbols · %d trading days in range · %.1fs",
            len(series), len(self._trading_days), dt,
        )

    # ---------- main loop ----------------------------------------------

    def run(self, strategy: Strategy) -> BacktestRun:
        self._load_panel()
        assert self._series is not None and self._universe is not None
        assert self._trading_days is not None
        cfg = self.config
        port = Portfolio.start(
            cfg.initial_cash,
            max_positions=cfg.max_positions,
            max_per_sector=cfg.max_per_sector,
            risk_per_trade_pct=cfg.risk_per_trade_pct,
            stop_loss_pct=cfg.stop_loss_pct,
        )
        equity_curve: list[dict[str, Any]] = []
        rebalance_dates = _pick_rebalance_dates(self._trading_days, strategy.rebalance)
        pending_orders: list[tuple[Candidate, dict[str, Any]]] = []

        # ---------- benchmark (equal-weight index of full universe) -----
        bench_symbols = list(self._universe.keys())
        bench = _equal_weight_index(
            bench_symbols, self._series, self._trading_days
        )

        t0 = time.time()
        for i, d in enumerate(self._trading_days):
            # ---- 1. Fill yesterday's pending orders at today's open ----
            if pending_orders:
                self._fill_pending(pending_orders, d, port)
                pending_orders = []

            # ---- 2. Mark + stop/target sweep on today's bar -----------
            closes_today = self._closes_on(d)
            self._exit_sweep(port, d)

            # ---- 3. Strategy decides at today's close, orders executed tomorrow
            is_last_day = i == len(self._trading_days) - 1
            if d in rebalance_dates and not is_last_day:
                view = PanelView(
                    _series=self._series,
                    _universe=self._universe,
                    _cutoff=d,
                )
                picks = strategy.pick(view, d)
                picks = [c for c in picks if c.symbol in self._series][: cfg.max_positions * 3]
                # Drop picks we already hold
                picks = [c for c in picks if c.symbol not in port.positions]
                pending_orders = [(c, {"decision_date": d}) for c in picks]

            # ---- 4. Record equity (EOD)
            eq = port.equity(closes_today)
            equity_curve.append({"date": d, "equity": eq, "cash": port.cash,
                                 "n_positions": len(port.positions)})
            if i > 0 and i % 252 == 0:
                logger.info(
                    "  ... %s  equity=%.0f  cash=%.0f  positions=%d  trades=%d",
                    d, eq, port.cash, len(port.positions), len(port.trades),
                )

        # ---- 5. Close everything on the last day at close -----------
        if self._trading_days:
            last = self._trading_days[-1]
            closes_last = self._closes_on(last)
            for sym in list(port.positions.keys()):
                price = closes_last.get(sym)
                if price is None:
                    continue
                eff = cfg.cost.sell_price(price)
                cash_in = cfg.cost.sell_cash(eff * port.positions[sym].shares)
                port.close_position(
                    symbol=sym, date=last, exit_price=eff,
                    cash_in=cash_in, exit_reason="end",
                )
            # re-record final equity after flat-out
            final_eq = port.equity(self._closes_on(last))
            equity_curve.append({
                "date": last, "equity": final_eq, "cash": port.cash,
                "n_positions": 0, "final_flatten": True,
            })

        dt = time.time() - t0
        logger.info("Backtest finished: %d trades · %.1fs", len(port.trades), dt)

        trades_dicts = [t.to_dict() for t in port.trades]
        metrics = compute_metrics(equity_curve, trades_dicts)
        attribution = _sector_attribution(port.trades)

        return BacktestRun(
            run_id=_new_run_id(),
            config=cfg,
            equity_curve=equity_curve,
            benchmark_curve=bench,
            trades=trades_dicts,
            metrics=metrics,
            sector_attribution=attribution,
            universe_size=len(self._universe),
            n_trading_days=len(self._trading_days),
        )

    # ---------- filling --------------------------------------------------

    def _fill_pending(
        self,
        pending: list[tuple[Candidate, dict[str, Any]]],
        today: str,
        port: Portfolio,
    ) -> None:
        cfg = self.config
        closes_today = self._closes_on(today)
        for cand, _meta in pending:
            if not port.has_room():
                break
            if cand.symbol in port.positions:
                continue
            bar = self._bar_on(cand.symbol, today)
            if bar is None or not bar.get("open") or bar["open"] <= 0:
                continue
            meta = self._universe.get(cand.symbol)
            if meta is None:
                continue
            if port.sector_count(meta["sector"]) >= port.max_per_sector:
                continue
            entry = cfg.cost.buy_price(float(bar["open"]))
            equity = port.equity(closes_today)
            shares = port.plan_shares(equity=equity, entry_price=entry)
            if shares <= 0:
                continue
            notional = entry * shares
            cash_out = cfg.cost.buy_cash(notional)
            if cash_out > port.cash:
                continue
            port.open_position(
                symbol=cand.symbol,
                name=meta["name"],
                sector=meta["sector"],
                date=today,
                entry_price=entry,
                shares=shares,
                cash_out=cash_out,
                reason=cand.reason,
                target_mult=cand.target_mult,
            )

    # ---------- stop/target sweep ----------------------------------------

    def _exit_sweep(self, port: Portfolio, today: str) -> None:
        cfg = self.config
        # iterate over a snapshot, because close_position mutates dict
        for sym, pos in list(port.positions.items()):
            bar = self._bar_on(sym, today)
            if bar is None:
                continue
            low = bar.get("low") or bar.get("close") or pos.stop_price
            high = bar.get("high") or bar.get("close") or 0.0
            # stop hit first (conservative: assume stop loss before target)
            if low <= pos.stop_price:
                fill = min(bar.get("open") or pos.stop_price, pos.stop_price)
                eff = cfg.cost.sell_price(fill)
                cash_in = cfg.cost.sell_cash(eff * pos.shares)
                port.close_position(
                    symbol=sym, date=today, exit_price=eff,
                    cash_in=cash_in, exit_reason="stop",
                )
                continue
            if pos.target_price and high >= pos.target_price:
                fill = max(bar.get("open") or pos.target_price, pos.target_price)
                eff = cfg.cost.sell_price(fill)
                cash_in = cfg.cost.sell_cash(eff * pos.shares)
                port.close_position(
                    symbol=sym, date=today, exit_price=eff,
                    cash_in=cash_in, exit_reason="target",
                )

    # ---------- saving ---------------------------------------------------

    def save(self, run: BacktestRun, *, out_dir: Path | None = None) -> Path:
        dest = out_dir or DEFAULT_DB_OUT
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"run_{run.run_id}.json"
        path.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved: %s", path)
        return path

    # ---------- helpers --------------------------------------------------

    def _closes_on(self, day: str) -> dict[str, float]:
        assert self._by_date is not None
        bars = self._by_date.get(day, {})
        return {
            sym: float(bar["close"])
            for sym, bar in bars.items()
            if bar.get("close")
        }

    def _bar_on(self, symbol: str, day: str) -> dict[str, Any] | None:
        assert self._by_date is not None
        return self._by_date.get(day, {}).get(symbol)


