"""Portfolio + position bookkeeping for the backtest engine.

Keeps things deterministic and cheap: a dict of open positions, a log
of closed trades, plus a running cash balance and equity snapshot.

Hard rules enforced here (per backtest.md):

    * max_positions          — default 10
    * max_per_sector         — default 3
    * risk_per_trade_pct     — default 0.02 (2 % of equity)
    * stop_loss_pct          — default 0.07 (-7 %)

Sizing = (equity * risk_per_trade_pct) / stop_loss_pct, capped at
(equity / max_positions). This yields roughly equal notional slots
while still respecting the per-trade risk budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Position:
    symbol: str
    name: str
    sector: str
    entry_date: str
    entry_price: float        # effective (post-slippage) entry
    shares: int
    stop_price: float
    target_price: float | None
    reason: str

    @property
    def cost_basis(self) -> float:
        return self.entry_price * self.shares

    def mark_to_market(self, close: float) -> float:
        return close * self.shares

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trade:
    symbol: str
    name: str
    sector: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str          # 'stop' | 'target' | 'signal' | 'rebalance' | 'end'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Portfolio:
    initial_cash: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)

    max_positions: int = 10
    max_per_sector: int = 3
    risk_per_trade_pct: float = 0.02
    stop_loss_pct: float = 0.07

    # ------------------------------------------------------------------ #
    # Construction                                                       #
    # ------------------------------------------------------------------ #

    @classmethod
    def start(
        cls,
        initial_cash: float,
        *,
        max_positions: int = 10,
        max_per_sector: int = 3,
        risk_per_trade_pct: float = 0.02,
        stop_loss_pct: float = 0.07,
    ) -> "Portfolio":
        return cls(
            initial_cash=initial_cash,
            cash=initial_cash,
            max_positions=max_positions,
            max_per_sector=max_per_sector,
            risk_per_trade_pct=risk_per_trade_pct,
            stop_loss_pct=stop_loss_pct,
        )

    # ------------------------------------------------------------------ #
    # Accessors                                                          #
    # ------------------------------------------------------------------ #

    def equity(self, closes: dict[str, float]) -> float:
        """Cash + mark-to-market of open positions using ``closes``."""
        mtm = 0.0
        for sym, pos in self.positions.items():
            c = closes.get(sym)
            if c is None or c <= 0:
                mtm += pos.cost_basis  # unknown → assume flat
            else:
                mtm += pos.mark_to_market(c)
        return self.cash + mtm

    def has_room(self) -> bool:
        return len(self.positions) < self.max_positions

    def sector_count(self, sector: str) -> int:
        return sum(1 for p in self.positions.values() if p.sector == sector)

    # ------------------------------------------------------------------ #
    # Sizing                                                             #
    # ------------------------------------------------------------------ #

    def plan_shares(self, *, equity: float, entry_price: float) -> int:
        """Compute share count for a new position given risk budget."""
        if entry_price <= 0:
            return 0
        risk_budget = equity * self.risk_per_trade_pct
        stop_distance = entry_price * self.stop_loss_pct
        if stop_distance <= 0:
            return 0
        risk_based = int(risk_budget / stop_distance)
        slot_cap = int((equity / self.max_positions) / entry_price)
        shares = max(0, min(risk_based, slot_cap))
        # Don't buy more than we can afford with cash on hand (after costs)
        # — engine applies cost on top so trim to ~97% to be safe.
        max_affordable = int((self.cash * 0.97) / entry_price)
        return max(0, min(shares, max_affordable))

    # ------------------------------------------------------------------ #
    # Trade bookkeeping (called by engine)                               #
    # ------------------------------------------------------------------ #

    def open_position(
        self,
        *,
        symbol: str,
        name: str,
        sector: str,
        date: str,
        entry_price: float,
        shares: int,
        cash_out: float,
        reason: str,
        target_mult: float = 2.0,
    ) -> Position:
        stop_price = entry_price * (1.0 - self.stop_loss_pct)
        target_price = entry_price * (1.0 + self.stop_loss_pct * target_mult)
        pos = Position(
            symbol=symbol,
            name=name,
            sector=sector,
            entry_date=date,
            entry_price=entry_price,
            shares=shares,
            stop_price=stop_price,
            target_price=target_price,
            reason=reason,
        )
        self.positions[symbol] = pos
        self.cash -= cash_out
        return pos

    def close_position(
        self,
        *,
        symbol: str,
        date: str,
        exit_price: float,
        cash_in: float,
        exit_reason: str,
    ) -> Trade | None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None
        pnl = (exit_price - pos.entry_price) * pos.shares
        pnl_pct = (exit_price / pos.entry_price) - 1.0 if pos.entry_price > 0 else 0.0
        trade = Trade(
            symbol=pos.symbol,
            name=pos.name,
            sector=pos.sector,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=date,
            exit_price=exit_price,
            shares=pos.shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_days=_date_diff(pos.entry_date, date),
            exit_reason=exit_reason,
        )
        self.cash += cash_in
        self.trades.append(trade)
        return trade


def _date_diff(a: str, b: str) -> int:
    from datetime import date
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except ValueError:
        return 0
