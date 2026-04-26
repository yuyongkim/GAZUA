"""Shared helpers for research sub-routers — envelope + lazy RAG retriever."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Make packages/core importable without requiring `pip install -e .`.
_PKG_CORE = Path(__file__).resolve().parents[4] / "packages" / "core"
if str(_PKG_CORE) not in sys.path:
    sys.path.insert(0, str(_PKG_CORE))

logger = logging.getLogger("routers.research")


def envelope(data: Any = None, error: Any = None, ok: Optional[bool] = None) -> Dict[str, Any]:
    if ok is None:
        ok = error is None
    return {"ok": bool(ok), "data": data, "error": error}


# --------------------------------------------------------------------------- #
# RAG retriever (lazy singleton — shared with knowledge search)               #
# --------------------------------------------------------------------------- #

_RETRIEVER = None
_RETRIEVER_IMPORT_ERROR: Optional[str] = None


def get_retriever():
    """Lazy singleton for ky_core.rag.Retriever.

    Returns None if the module cannot be imported; inspect
    ``retriever_import_error()`` for the stringified exception.
    """
    global _RETRIEVER, _RETRIEVER_IMPORT_ERROR
    if _RETRIEVER is not None:
        return _RETRIEVER
    try:
        from ky_core.rag import Retriever  # type: ignore

        _RETRIEVER = Retriever()
        _RETRIEVER_IMPORT_ERROR = None
    except Exception as exc:  # ImportError or misc
        logger.warning("ky_core.rag import failed: %s", exc)
        _RETRIEVER_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        _RETRIEVER = None
    return _RETRIEVER


def retriever_import_error() -> Optional[str]:
    return _RETRIEVER_IMPORT_ERROR
