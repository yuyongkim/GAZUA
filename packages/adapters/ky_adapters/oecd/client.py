"""OECD SDMX REST adapter — Composite Leading Indicator + macro series.

No API key required. Uses the post-2024 SDMX 3.0 endpoint at sdmx.oecd.org.
The legacy stats.oecd.org is being deprecated.

Common dataflows we need:
  - DSD_CLI@DF_CLI       — Composite Leading Indicator (선행지수, 6-9개월 선행)
  - DSD_BCI@DF_BCI       — Business Confidence Indicator
  - DSD_CCI@DF_CCI       — Consumer Confidence Indicator
  - DSD_PMI@DF_PMI       — Manufacturing PMI
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

OECD_BASE = "https://sdmx.oecd.org/public/rest/data"
# Default dataflow for the CLI series — most commonly used.
DEFAULT_CLI_DATAFLOW = "OECD.SDD.STES,DSD_STES@DF_CLI,4.0"


@dataclass
class OECDObservation:
    series_id: str
    period: str        # YYYY-MM or YYYY-Q
    value: Optional[float]
    unit: Optional[str] = None
    raw: dict[str, Any] | None = None

    def as_row(self, source_id: str = "oecd") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "series_id": self.series_id,
            "period": self.period,
            "value": self.value,
            "unit": self.unit,
        }


class OECDAdapter(BaseAdapter):
    source_id = "oecd"
    priority = 3

    def __init__(self, base_url: str = OECD_BASE) -> None:
        super().__init__()
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "OECDAdapter":
        return cls()

    # ----- Contract ---------------------------------------------------

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            obs = self.get_cli(country_iso="KOR", recent=3)
            ok = isinstance(obs, list)
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(obs)})

    # ----- Public methods --------------------------------------------

    def get_cli(
        self,
        country_iso: str = "KOR",
        recent: int = 60,
    ) -> list[OECDObservation]:
        """Composite Leading Indicator — 가장 자주 쓰이는 OECD 시리즈.
        6-9개월 선행. country_iso는 ISO3 (KOR/USA/CHN/JPN/DEU/...)."""
        # Key format: <country>.<measure>.<freq>...
        # CLI default measure: LI (amplitude-adjusted), unit: IX (index)
        key = f"{country_iso}.M.LI...AA...H"
        return self._call(DEFAULT_CLI_DATAFLOW, key, recent_periods=recent)

    def get_series(
        self,
        dataflow: str,
        key: str,
        start_period: Optional[str] = None,
        end_period: Optional[str] = None,
        recent_periods: Optional[int] = None,
    ) -> list[OECDObservation]:
        """Generic SDMX query. dataflow ≈ 'OECD.SDD.STES,DSD_STES@DF_CLI,4.0',
        key ≈ 'KOR.M.LI...AA...H'."""
        return self._call(dataflow, key, start_period, end_period, recent_periods)

    # ----- Internals --------------------------------------------------

    def _call(
        self,
        dataflow: str,
        key: str,
        start_period: Optional[str] = None,
        end_period: Optional[str] = None,
        recent_periods: Optional[int] = None,
    ) -> list[OECDObservation]:
        url = f"{self.base_url}/{dataflow}/{key}"
        params: dict[str, Any] = {"format": "jsondata"}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period
        if recent_periods:
            params["lastNObservations"] = recent_periods

        resp = self._request(
            "GET",
            url,
            params=params,
            headers={"Accept": "application/vnd.sdmx.data+json;version=1.0.0"},
        )
        if resp.status_code != 200:
            raise AdapterError(
                f"OECD {dataflow} → HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"OECD response not JSON: {exc}") from exc

        return self._parse_sdmx_json(payload, series_id=f"{dataflow}/{key}")

    @staticmethod
    def _parse_sdmx_json(payload: dict, *, series_id: str) -> list[OECDObservation]:
        """SDMX-JSON 2.0 (current OECD) wraps everything under ``data``,
        time values live at ``data.structures[0].dimensions.observation[0].values``.
        We also handle the older 1.0 ``data.structure.dimensions.observation``
        path for backward-compat."""
        data = payload.get("data") or payload

        # 2.0 path uses plural `structures`; 1.0 uses singular `structure`.
        structures = data.get("structures")
        if isinstance(structures, list) and structures:
            obs_dim_root = structures[0]
        else:
            obs_dim_root = data.get("structure", {})
        time_values = (
            obs_dim_root.get("dimensions", {})
            .get("observation", [{}])[0]
            .get("values", [])
        )

        datasets = data.get("dataSets") or []
        if not datasets:
            return []

        def _period_for(idx: int) -> str:
            if 0 <= idx < len(time_values):
                v = time_values[idx]
                return v.get("id") or v.get("name") or str(idx)
            return str(idx)

        # Series-keyed form
        series = datasets[0].get("series", {})
        out: list[OECDObservation] = []
        if series:
            first_key = next(iter(series))
            observations = series[first_key].get("observations", {})
            for k, v in observations.items():
                try:
                    idx = int(k)
                except ValueError:
                    idx = int(k.split(":")[0])
                out.append(
                    OECDObservation(
                        series_id=series_id,
                        period=_period_for(idx),
                        value=v[0] if v else None,
                    )
                )
            return out

        # Observation-flat form
        obs_flat = datasets[0].get("observations", {})
        for k, v in obs_flat.items():
            idx = int(k.split(":")[0])
            out.append(
                OECDObservation(
                    series_id=series_id,
                    period=_period_for(idx),
                    value=v[0] if v else None,
                )
            )
        return out
