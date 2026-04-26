"""ML forecasting baseline — 매트릭스 cells × time → next-quarter sector ETF return.

Stdlib OLS multilinear regression baseline. scikit-learn / XGBoost 향후 추가.

Walk-forward:
  - 분기 t의 매트릭스 snapshot (sector × indicator z-score)
  - target: 분기 t+1 sector ETF 평균 수익률
  - rolling fit (last 16 quarters) → predict t+1
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ForecastResult:
    sector_num: str
    sector_name: str
    forecast_return_pct: float    # next quarter
    confidence: float             # 0..1 (R^2 in-sample)
    n_train_samples: int
    method: str = "OLS"


def _ols_predict(
    X: list[list[float]],
    y: list[float],
    x_new: list[float],
) -> tuple[float | None, float | None]:
    """Multivariate OLS via normal equations. Returns (prediction, R^2)."""
    n = len(X)
    if n < 4 or not X[0]:
        return None, None
    p = len(X[0])
    # Add intercept
    X_aug = [[1.0] + row for row in X]
    p_aug = p + 1

    # Compute X^T X (p_aug x p_aug)
    xtx = [[0.0] * p_aug for _ in range(p_aug)]
    for i in range(p_aug):
        for j in range(p_aug):
            xtx[i][j] = sum(X_aug[k][i] * X_aug[k][j] for k in range(n))

    # X^T y
    xty = [sum(X_aug[k][i] * y[k] for k in range(n)) for i in range(p_aug)]

    # Solve via Gaussian elimination
    A = [row[:] + [xty[i]] for i, row in enumerate(xtx)]
    # Forward elimination
    try:
        for i in range(p_aug):
            # find pivot
            pivot = i
            for k in range(i + 1, p_aug):
                if abs(A[k][i]) > abs(A[pivot][i]):
                    pivot = k
            A[i], A[pivot] = A[pivot], A[i]
            if abs(A[i][i]) < 1e-12:
                return None, None
            # eliminate
            for k in range(i + 1, p_aug):
                factor = A[k][i] / A[i][i]
                for j in range(i, p_aug + 1):
                    A[k][j] -= factor * A[i][j]
        # Back-substitution
        coef = [0.0] * p_aug
        for i in range(p_aug - 1, -1, -1):
            s = A[i][p_aug] - sum(A[i][j] * coef[j] for j in range(i + 1, p_aug))
            coef[i] = s / A[i][i]
    except Exception:
        return None, None

    # Predict for x_new
    pred = coef[0] + sum(coef[i + 1] * x_new[i] for i in range(p))

    # R^2 in-sample
    y_mean = sum(y) / n
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    y_hat = [coef[0] + sum(coef[j + 1] * X[k][j] for j in range(p)) for k in range(n)]
    ss_res = sum((y[k] - y_hat[k]) ** 2 for k in range(n))
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return pred, max(0.0, min(1.0, r2))


def forecast_next_quarter() -> list[ForecastResult]:
    """매트릭스 → 각 sector의 next quarter ETF return 예측.

    Note: 단일 OLS baseline. 실제로는 walk-forward + sklearn 필요.
    이 모듈은 framework 검증용 — production 전 cross-val 추가.
    """
    from ky_core.scoring.sector_leadlag import _build_sector_series
    from ky_core.scoring.sector_rotation_backtest import (
        SECTOR_ETF_SLUG, _read_etf_prices, _monthly_returns,
    )
    from ky_core.scoring.macro_sector_strength import _parse_map

    sec_series = _build_sector_series(method="yoy")
    sec_names = {c["sector_num"]: c["sector_name"] for c in _parse_map()}

    # Build X (each row: avg sector z at quarter t)
    # y: next-quarter avg ETF return for that sector
    results: list[ForecastResult] = []
    for sec, etf_pair in SECTOR_ETF_SLUG.items():
        if sec not in sec_series:
            continue
        sig = sec_series[sec]
        if len(sig) < 16:
            continue
        # Quarterize: 3 monthly observations / quarter
        sig_q = [sum(sig[i:i+3]) / 3 for i in range(0, len(sig), 3) if len(sig[i:i+3]) == 3]
        if len(sig_q) < 12:
            continue

        folder, slug = etf_pair
        prices = _read_etf_prices(folder, slug)
        monthly_r = _monthly_returns(prices)
        # Quarterly returns: aggregate by 3 months
        sorted_months = sorted(monthly_r.keys())
        quarter_r = []
        for i in range(0, len(sorted_months), 3):
            qm = sorted_months[i:i+3]
            if len(qm) < 3:
                continue
            r_acc = 1.0
            for m in qm:
                r_acc *= (1 + monthly_r[m])
            quarter_r.append(r_acc - 1)
        if len(quarter_r) < 12:
            continue

        # Align: pair sig_q[i] → quarter_r[i+1]
        n_pairs = min(len(sig_q) - 1, len(quarter_r) - 1)
        if n_pairs < 8:
            continue
        X = [[sig_q[i]] for i in range(-n_pairs - 1, -1)]
        y = [quarter_r[i] for i in range(-n_pairs, 0)]
        x_new = [sig_q[-1]]
        pred, r2 = _ols_predict(X, y, x_new)
        if pred is None:
            continue

        results.append(ForecastResult(
            sector_num=sec,
            sector_name=sec_names.get(sec, sec),
            forecast_return_pct=round(pred * 100, 2),
            confidence=round(r2 if r2 is not None else 0, 3),
            n_train_samples=n_pairs,
        ))

    results.sort(key=lambda r: r.forecast_return_pct, reverse=True)
    return results
