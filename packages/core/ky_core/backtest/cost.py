"""Trading-cost model for the KR market.

The default values follow the `backtest.md` contract:

    buy commission   : 0.015%
    sell commission  : 0.015%
    slippage         : 0.10% per side
    transaction tax  : 0.18% on sell proceeds

Every backtest writes its own CostModel into the run artefact so the
numbers remain auditable regardless of future tuning.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class CostModel:
    """Korean-market default trading costs.

    Values are expressed as decimals, so 0.00015 == 0.015%.
    """

    buy_commission: float = 0.00015
    sell_commission: float = 0.00015
    slippage: float = 0.001
    sell_tax: float = 0.0018  # 증권거래세

    # ---- price helpers ---------------------------------------------------

    def buy_price(self, fill_price: float) -> float:
        """Effective buy price after slippage. Multiplies by (1 + slippage)."""
        return fill_price * (1.0 + self.slippage)

    def sell_price(self, fill_price: float) -> float:
        """Effective sell price after slippage. Multiplies by (1 - slippage)."""
        return fill_price * (1.0 - self.slippage)

    # ---- cash impact -----------------------------------------------------

    def buy_cash(self, notional: float) -> float:
        """Cash outflow for a buy (post-slippage notional + commission)."""
        return notional * (1.0 + self.buy_commission)

    def sell_cash(self, notional: float) -> float:
        """Cash inflow for a sell (post-slippage notional less tax + commission)."""
        return notional * (1.0 - self.sell_commission - self.sell_tax)

    def roundtrip_drag_pct(self) -> float:
        """Approximate drag per completed round-trip, in decimal.

        Useful for quick sanity checks — a 1% move must cover ~0.42 %
        of fixed costs before it shows as profit.
        """
        return (
            2.0 * self.slippage
            + self.buy_commission
            + self.sell_commission
            + self.sell_tax
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_COST = CostModel()
