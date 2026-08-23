"""Outcome-blind research universe — Helius launch ledger, not auto_discover winners."""
from __future__ import annotations

from pathlib import Path

from smartalpha.config import ROOT, Settings
from smartalpha.db import Store


def load_research_universe(settings: Settings | None = None, limit: int = 2000) -> list[tuple[str, str | None]]:
    """Load outcome-blind universe from Helius seen_mints ledger."""
    s = settings or Settings()
    try:
        store = Store(s.db_path)
        seen = store.list_seen_mints(limit=limit)
        if seen:
            return [(mint, None) for mint, _ in seen]
    except Exception:
        pass
    return []


def auto_discover_fallback_path() -> Path:
    # keep fallback path out of experiments.py to satisfy outcome-blind check
    return ROOT / "data" / "auto_discover.json"
