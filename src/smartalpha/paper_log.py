from __future__ import annotations

import asyncio
import csv
import json
import time
from dataclasses import dataclass

from smartalpha.config import Settings
from smartalpha.db import Store
from smartalpha.funder import dex_price_snapshot
from smartalpha.signal_rules import (
    classify_signal,
    hot_organic_buyers,
    should_follow_launch,
)


@dataclass
class PaperSignalInput:
    mint: str
    signal_ts: int
    creator: str
    signature: str
    recommendation: str
    copytrap_risk: str
    intel: object
    liquidity_usd: float | None
    notes: list[str]


def log_paper_signal(
    inp: PaperSignalInput,
    *,
    settings: Settings | None = None,
    store: Store | None = None,
) -> int:
    """Always persist a paper row — snapshot/scoring failures must not drop the signal."""
    s = settings or Settings()
    store = store or Store(s.db_path)
    extra_notes: list[str] = []

    snap0: dict = {}
    try:
        snap0 = dex_price_snapshot(inp.mint) or {}
    except Exception as exc:
        extra_notes.append(f"snap0_err={type(exc).__name__}")

    try:
        strict = should_follow_launch(
            inp.intel,
            min_hot_buyers=s.signal_min_hot_buyers,
            liquidity_usd=inp.liquidity_usd,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            allow_unknown_liq=s.signal_allow_unknown_liq,
            ignore_stale_low_liq=False,  # live paper: current liq is entry-relevant
        )
    except Exception as exc:
        strict = False
        extra_notes.append(f"strict_err={type(exc).__name__}")

    try:
        level = classify_signal(
            inp.intel,
            min_hot_buyers=s.signal_min_hot_buyers,
            liquidity_usd=inp.liquidity_usd,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            allow_unknown_liq=s.signal_allow_unknown_liq,
            ignore_stale_low_liq=False,
        ).value
    except Exception:
        level = "unknown"
    extra_notes.append(f"level={level}")

    try:
        hot_n = len(hot_organic_buyers(inp.intel))
    except Exception:
        hot_n = 0
    try:
        hot_list = list(getattr(inp.intel, "hot_funder_hits", None) or [])
    except Exception:
        hot_list = []

    delays = s.paper_snapshot_delays or (0, 90, 180, 300, 900)
    snapshots: dict[str, dict] = {}
    if 0 in delays or not delays:
        snapshots["0"] = snap0

    notes = list(inp.notes[:8]) + extra_notes
    store.upsert_paper_signal(
        mint=inp.mint,
        signal_ts=inp.signal_ts,
        creator=inp.creator,
        signature=inp.signature,
        recommendation=inp.recommendation,
        copytrap_risk=inp.copytrap_risk,
        hot_organic_buyers=hot_n,
        hot_funders=hot_list,
        liquidity_usd=inp.liquidity_usd,
        strict_signal=strict,
        price_usd=snap0.get("price_usd"),
        snapshots=snapshots,
        notes="; ".join(notes),
    )
    return inp.signal_ts


async def schedule_paper_snapshots(
    mint: str,
    signal_ts: int,
    *,
    settings: Settings | None = None,
    store: Store | None = None,
) -> None:
    s = settings or Settings()
    store = store or Store(s.db_path)
    delays = [d for d in (s.paper_snapshot_delays or (90, 180, 300, 900)) if d > 0]
    pending = set(delays)
    while pending:
        now = time.time()
        for delay in list(pending):
            if now < signal_ts + delay:
                continue
            try:
                snap = await asyncio.to_thread(dex_price_snapshot, mint)
            except Exception:
                snap = None
            if snap:
                _append_snapshot(store, mint, str(delay), snap)
            pending.discard(delay)
        if pending:
            next_due = min(signal_ts + d for d in pending)
            await asyncio.sleep(max(1.0, min(30.0, next_due - time.time())))


def catch_up_paper_snapshots(
    *,
    settings: Settings | None = None,
    store: Store | None = None,
) -> dict:
    """Fill missing delayed snapshots (for cron / paper-snapshot CLI)."""
    s = settings or Settings()
    store = store or Store(s.db_path)
    delays = [d for d in (s.paper_snapshot_delays or (90, 180, 300, 900)) if d > 0]
    updated = 0
    errors = 0
    now = int(time.time())
    rows = store.list_paper_signals(limit=500)
    for row in rows:
        mint = row["mint"]
        signal_ts = int(row["signal_ts"])
        snaps = json.loads(row.get("snapshots_json") or "{}")
        for delay in delays:
            key = str(delay)
            if key in snaps:
                continue
            if now < signal_ts + delay:
                continue
            try:
                snap = dex_price_snapshot(mint)
            except Exception:
                errors += 1
                continue
            if snap:
                _append_snapshot(store, mint, key, snap)
                updated += 1
    return {
        "updated": updated,
        "checked": len(rows),
        "errors": errors,
        "paper_rows": len(rows),
        "strict": sum(1 for r in rows if r.get("strict_signal")),
    }


def paper_health(
    *,
    settings: Settings | None = None,
    store: Store | None = None,
) -> dict:
    """Quick Phase2 readiness snapshot."""
    s = settings or Settings()
    store = store or Store(s.db_path)
    rows = store.list_paper_signals(limit=2000)
    strict = [r for r in rows if r.get("strict_signal")]
    with_price = [r for r in rows if r.get("price_usd")]
    delays = [d for d in (s.paper_snapshot_delays or (90, 180, 300, 900)) if d > 0]
    complete = 0
    for r in rows:
        snaps = json.loads(r.get("snapshots_json") or "{}")
        if all(str(d) in snaps for d in delays):
            complete += 1
    latest_ts = max((int(r["signal_ts"]) for r in rows), default=0)
    return {
        "paper_rows": len(rows),
        "strict_rows": len(strict),
        "with_price_t0": len(with_price),
        "full_snapshots": complete,
        "latest_signal_age_sec": (int(time.time()) - latest_ts) if latest_ts else None,
        "db_path": str(s.db_path),
        "ready_for_prove_phase2": len(strict) >= 30 and complete >= 20,
    }


def _append_snapshot(store: Store, mint: str, key: str, snap: dict) -> None:
    row = store.get_paper_signal(mint)
    if not row:
        return
    snaps = json.loads(row.get("snapshots_json") or "{}")
    snaps[key] = snap
    store.upsert_paper_signal(
        mint=mint,
        signal_ts=int(row["signal_ts"]),
        creator=row.get("creator") or "",
        signature=row.get("signature") or "",
        recommendation=row.get("recommendation") or "",
        copytrap_risk=row.get("copytrap_risk") or "",
        hot_organic_buyers=int(row.get("hot_organic_buyers") or 0),
        hot_funders=json.loads(row.get("hot_funders_json") or "[]"),
        liquidity_usd=row.get("liquidity_usd"),
        strict_signal=bool(row.get("strict_signal")),
        price_usd=row.get("price_usd"),
        snapshots=snaps,
        notes=row.get("notes") or "",
    )


def export_paper_csv(
    path,
    *,
    settings: Settings | None = None,
    store: Store | None = None,
) -> int:
    s = settings or Settings()
    store = store or Store(s.db_path)
    rows = store.list_paper_signals(limit=1000)
    delays = s.paper_snapshot_delays or (0, 90, 180, 300, 900)
    fieldnames = [
        "signal_ts",
        "mint",
        "strict_signal",
        "recommendation",
        "copytrap_risk",
        "hot_organic_buyers",
        "liquidity_usd",
        "price_t0_usd",
    ]
    for d in delays:
        fieldnames.extend([f"gain_{d}s_pct", f"price_{d}s_usd", f"liq_{d}s_usd"])

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            snaps = json.loads(row.get("snapshots_json") or "{}")
            p0 = row.get("price_usd")
            if p0 is None:
                p0 = (snaps.get("0") or {}).get("price_usd")
            out: dict = {
                "signal_ts": row["signal_ts"],
                "mint": row["mint"],
                "strict_signal": row["strict_signal"],
                "recommendation": row["recommendation"],
                "copytrap_risk": row["copytrap_risk"],
                "hot_organic_buyers": row["hot_organic_buyers"],
                "liquidity_usd": row["liquidity_usd"],
                "price_t0_usd": p0,
            }
            for d in delays:
                snap = snaps.get(str(d), {})
                px = snap.get("price_usd")
                # True delay tax: price change from signal t0, not Dex rolling m5/h1.
                gain = None
                if p0 is not None and px is not None and float(p0) > 0:
                    gain = round((float(px) / float(p0) - 1.0) * 100.0, 4)
                out[f"gain_{d}s_pct"] = gain
                out[f"price_{d}s_usd"] = px
                out[f"liq_{d}s_usd"] = snap.get("liquidity_usd")
            w.writerow(out)
    return len(rows)
