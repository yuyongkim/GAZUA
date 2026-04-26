"""Macro cycle clustering — 한국 경제 사이클 국면 분류.

분기별 매크로 snapshot vector (각 cell의 z-score) → K-means 클러스터.
N_CLUSTERS=4 default → 회복/확장/둔화/침체 4국면.

Stdlib 만으로 구현 (scikit-learn 불필요).
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


from ky_core.scoring.macro_sector_strength import (  # noqa: E402
    _parse_map,
    _disk_index,
    _resolve_cell,
    _read_values,
    _yoy_change,
)


@dataclass
class CycleRegime:
    cluster_id: int
    label: str            # auto-generated based on signal direction
    n_periods: int
    avg_signals: dict[str, float]   # mean YoY% by sector
    representative_quarters: list[str]


@dataclass
class CycleSnapshot:
    quarter: str
    cluster_id: int
    sector_signals: dict[str, float]


@dataclass
class CycleResult:
    n_clusters: int
    regimes: list[CycleRegime]
    snapshots: list[CycleSnapshot]
    n_quarters: int
    n_features: int
    label_for_current: str | None = None


# -- Quarterly aggregation ---------------------------------------------------


def _to_quarterly(values: list[float], freq_hint: str) -> dict[str, float]:
    """Convert raw values to quarterly mean. freq_hint: 'D', 'M', 'Q'.
    Quarter format: 'YYYYQn'. Without dates, just bucket by sequential index.
    """
    if not values:
        return {}
    n = len(values)
    # Approximate: D=63 obs per quarter, M=3, Q=1
    bucket = {"D": 63, "M": 3, "Q": 1}.get(freq_hint, 3)
    out: dict[str, float] = {}
    for i in range(n):
        q_idx = i // bucket
        key = f"q{q_idx:03d}"
        out.setdefault(key, [])
        out[key].append(values[i])
    return {k: sum(v) / len(v) for k, v in out.items() if v}


def _detect_freq(n: int) -> str:
    if n > 1500:
        return "D"
    if n > 100:
        return "M"
    return "Q"


def _build_feature_matrix() -> tuple[list[str], list[str], list[list[float]]]:
    """Build (sector_keys, quarter_keys, matrix[quarter][sector]).

    Each cell: sector mean YoY% z-score per quarter.
    Missing cells filled with 0.0 (mean).
    """
    cells = _parse_map()
    csvs = _disk_index()
    sec_names = {c["sector_num"]: c["sector_name"] for c in cells}

    # sector → list of yoy series (one per cell)
    by_sector: dict[str, list[list[float]]] = {}
    by_sector_freq: dict[str, str] = {}
    for c in cells:
        match = _resolve_cell(c, csvs)
        if not match:
            continue
        folder, slug = match
        values = _read_values(folder, slug)
        if not values or len(values) < 36:
            continue
        yoy = _yoy_change(values)
        if len(yoy) < 12:
            continue
        by_sector.setdefault(c["sector_num"], []).append(yoy)
        by_sector_freq[c["sector_num"]] = _detect_freq(len(values))

    if not by_sector:
        return [], [], []

    # Per-sector quarterly aggregate
    sec_quarterly: dict[str, dict[str, float]] = {}
    for sec, cells_yoy in by_sector.items():
        freq = by_sector_freq.get(sec, "M")
        # Aggregate per cell to quarterly, then average across cells
        per_cell_q = [_to_quarterly(yoy, freq) for yoy in cells_yoy]
        all_qs = set()
        for d in per_cell_q:
            all_qs.update(d.keys())
        avg: dict[str, float] = {}
        for q in all_qs:
            vals = [d[q] for d in per_cell_q if q in d]
            if vals:
                avg[q] = sum(vals) / len(vals)
        sec_quarterly[sec] = avg

    # Common quarters: take last 80 quarters (~20 years) for clustering
    all_quarters = set()
    for d in sec_quarterly.values():
        all_quarters.update(d.keys())
    quarters = sorted(all_quarters)[-80:]

    sec_keys = sorted(sec_quarterly.keys())
    matrix = []
    for q in quarters:
        row = []
        for sec in sec_keys:
            v = sec_quarterly[sec].get(q, 0.0)
            row.append(v if math.isfinite(v) else 0.0)
        matrix.append(row)

    return sec_keys, quarters, matrix


# -- K-means (stdlib) --------------------------------------------------------


def _euclidean(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _mean_vec(vecs: list[list[float]]) -> list[float]:
    if not vecs:
        return []
    n = len(vecs[0])
    return [sum(v[i] for v in vecs) / len(vecs) for i in range(n)]


def _kmeans(
    matrix: list[list[float]],
    k: int,
    max_iter: int = 100,
    seed: int = 42,
) -> tuple[list[int], list[list[float]]]:
    """Simple k-means. Returns (labels, centroids)."""
    n = len(matrix)
    if n == 0 or k <= 0:
        return [], []
    rng = random.Random(seed)
    # Init: random sample
    centroids = [matrix[i][:] for i in rng.sample(range(n), min(k, n))]
    while len(centroids) < k:
        centroids.append(matrix[rng.randint(0, n - 1)][:])

    labels = [0] * n
    for _ in range(max_iter):
        new_labels = []
        for v in matrix:
            d_min = float("inf")
            l_best = 0
            for j, c in enumerate(centroids):
                d = _euclidean(v, c)
                if d < d_min:
                    d_min = d
                    l_best = j
            new_labels.append(l_best)
        if new_labels == labels:
            break
        labels = new_labels
        # Recompute centroids
        for j in range(k):
            members = [matrix[i] for i in range(n) if labels[i] == j]
            if members:
                centroids[j] = _mean_vec(members)
    return labels, centroids


# -- Auto-label clusters -----------------------------------------------------


def _label_cluster(centroid: list[float], sec_keys: list[str]) -> str:
    """Based on centroid signals, auto-assign label.

    - 평균 z > +0.5 → '확장' (모든 섹터 강세)
    - 평균 z < -0.5 → '침체'
    - 산업재 (반도체/화학/철강) 강세 + 매크로 약세 → '회복'
    - 산업재 약세 + 매크로 강세 → '둔화'
    - 그 외 → '중립'
    """
    if not centroid:
        return "unknown"
    avg = sum(centroid) / len(centroid)
    if avg > 0.5:
        return "확장"
    if avg < -0.5:
        return "침체"
    # Industrial: 02 화학, 04 철강, 26 반도체, 24 디스플레이
    industrial_idx = [i for i, s in enumerate(sec_keys) if s in {"02", "04", "26", "24", "10"}]
    if industrial_idx:
        ind_avg = sum(centroid[i] for i in industrial_idx) / len(industrial_idx)
        if ind_avg > 0.3:
            return "회복"
        if ind_avg < -0.3:
            return "둔화"
    return "중립"


def compute_macro_cycle(n_clusters: int = 4) -> CycleResult:
    sec_keys, quarters, matrix = _build_feature_matrix()
    if not matrix:
        return CycleResult(n_clusters=0, regimes=[], snapshots=[],
                           n_quarters=0, n_features=0)

    labels, centroids = _kmeans(matrix, n_clusters)
    sec_names = {c["sector_num"]: c["sector_name"] for c in _parse_map()}

    # Build snapshots
    snapshots: list[CycleSnapshot] = []
    for i, q in enumerate(quarters):
        sig = {sec_names.get(sec_keys[j], sec_keys[j]): round(matrix[i][j], 2)
               for j in range(len(sec_keys))}
        snapshots.append(CycleSnapshot(
            quarter=q, cluster_id=labels[i], sector_signals=sig,
        ))

    # Build regimes
    regimes: list[CycleRegime] = []
    for cid in range(n_clusters):
        members_idx = [i for i, l in enumerate(labels) if l == cid]
        if not members_idx:
            continue
        # Avg signals per sector
        avg_sig = {sec_names.get(sec_keys[j], sec_keys[j]): round(centroids[cid][j], 2)
                   for j in range(len(sec_keys))}
        rep_quarters = [quarters[i] for i in members_idx[-3:]]  # most recent 3
        regimes.append(CycleRegime(
            cluster_id=cid,
            label=_label_cluster(centroids[cid], sec_keys),
            n_periods=len(members_idx),
            avg_signals=avg_sig,
            representative_quarters=rep_quarters,
        ))

    current_label = None
    if snapshots:
        cur_id = snapshots[-1].cluster_id
        for r in regimes:
            if r.cluster_id == cur_id:
                current_label = r.label
                break

    return CycleResult(
        n_clusters=n_clusters,
        regimes=regimes,
        snapshots=snapshots,
        n_quarters=len(quarters),
        n_features=len(sec_keys),
        label_for_current=current_label,
    )
