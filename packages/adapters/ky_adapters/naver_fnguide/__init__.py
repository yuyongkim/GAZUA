"""Naver / FnGuide snapshot adapter.

This adapter pulls a compact fundamentals snapshot (consensus target, peer
comparables, ownership, most recent financial highlights) from
``m.stock.naver.com`` JSON endpoints. When the mobile JSON schema changes or
a field is missing we fall back to the static HTML exposed by
``comp.fnguide.com``.

Policy reminder — bulk scraping is forbidden; the adapter enforces a
per-symbol 10 minute cache via the ``fnguide_snapshots`` table in ky.db.
"""
from __future__ import annotations

from ky_adapters.naver_fnguide.client import FnguideAdapter, FnguideSnapshot

__all__ = ["FnguideAdapter", "FnguideSnapshot"]
