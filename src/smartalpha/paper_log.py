from __future__ import annotations

import asyncio
import csv
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from smartalpha.config import Settings
from smartalpha.db import Store
from smartalpha.providers.dexscreener import get_pair_meta
from smartalpha.signal_rules import calculate_friction_net_gain, classify_signal


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
    buy_count: int = 0
    sell_count: int = 0
    top_buyer_share: float = 0.0
    volume_usd: float | None = None


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
        snap0 = get_pair_meta(inp.mint) or {}
    except Exception as exc:
        extra_notes.append(f"snap0_err={type(exc).__name__}")

    try:
        level = classify_signal(
            inp.intel,
            liquidity_usd=inp.liquidity_usd,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            volume_usd=inp.volume_usd,
            min_velocity=s.signal_min_velocity,
            min_buy_sell_ratio=s.signal_min_buy_sell_ratio,
            max_buyer_share=s.signal_max_buyer_share,
            allow_unknown_liq=s.signal_allow_unknown_liq,
            allow_unknown_velocity=s.signal_allow_unknown_velocity,
            ignore_stale_low_liq=False,
        ).value
        strict = level == "strong"
    except Exception as exc:
        level = "unknown"
        strict = False
        extra_notes.append(f"signal_err={type(exc).__name__}")
    extra_notes.append(f"level={level}")

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
        liquidity_usd=inp.liquidity_usd,
        strict_signal=strict,
        price_usd=snap0.get("price_usd"),
        snapshots=snapshots,
        notes="; ".join(notes),
        buy_count=inp.buy_count,
        sell_count=inp.sell_count,
        top_buyer_share=inp.top_buyer_share,
        volume_usd=inp.volume_usd,
    )
    return inp.signal_ts


async def schedule_paper_snapshots(
    mint: str,
    signal_ts: int,
    *,
    settings: Settings | None = None,
    store: Store | None = None,
    snapshot_provider: Callable[[int], dict | None] | None = None,
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
            await asyncio.to_thread(_record_snapshot, store, mint, delay, snapshot_provider)
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
            result = _record_snapshot(store, mint, delay)
            if result is None:
                errors += 1
            elif result:
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


def _record_snapshot(
    store: Store,
    mint: str,
    delay: int,
    snapshot_provider: Callable[[int], dict | None] | None = None,
) -> bool | None:
    try:
        snap = snapshot_provider(delay) if snapshot_provider else get_pair_meta(mint)
    except Exception:
        return None
    if not snap:
        return False
    store.merge_paper_snapshot(mint, str(delay), snap)
    return True


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
        "buy_count",
        "sell_count",
        "top_buyer_share",
        "volume_usd",
        "liquidity_usd",
        "price_t0_usd",
    ]
    for d in delays:
        fieldnames.extend(
            [
                f"gain_{d}s_pct",
                f"gain_{d}s_net_pct",
                f"price_{d}s_usd",
                f"liq_{d}s_usd",
            ]
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            snaps = json.loads(row.get("snapshots_json") or "{}")
            p0 = row.get("price_usd")
            if p0 is None:
                p0 = (snaps.get("0") or {}).get("price_usd")
            row_liq = row.get("liquidity_usd")
            out: dict = {
                "signal_ts": row["signal_ts"],
                "mint": row["mint"],
                "strict_signal": row["strict_signal"],
                "recommendation": row["recommendation"],
                "copytrap_risk": row["copytrap_risk"],
                "buy_count": row.get("buy_count", 0),
                "sell_count": row.get("sell_count", 0),
                "top_buyer_share": row.get("top_buyer_share", 0.0),
                "volume_usd": row.get("volume_usd"),
                "liquidity_usd": row_liq,
                "price_t0_usd": p0,
            }
            for d in delays:
                snap = snaps.get(str(d), {})
                px = snap.get("price_usd")
                liq_d = snap.get("liquidity_usd") or row_liq
                gain = None
                net_gain = None
                if p0 is not None and px is not None and float(p0) > 0:
                    raw_ratio = float(px) / float(p0) - 1.0
                    gain = round(raw_ratio * 100.0, 4)
                    if liq_d is not None:
                        net_ratio = calculate_friction_net_gain(
                            raw_ratio,
                            float(liq_d),
                            trade_size_usd=getattr(s, "paper_trade_size_usd", 100.0),
                        )
                        net_gain = round(net_ratio * 100.0, 4)
                out[f"gain_{d}s_pct"] = gain
                out[f"gain_{d}s_net_pct"] = net_gain
                out[f"price_{d}s_usd"] = px
                out[f"liq_{d}s_usd"] = snap.get("liquidity_usd")
            w.writerow(out)
    return len(rows)
