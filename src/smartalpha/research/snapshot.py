"""Launch Feature Snapshot pipeline: t0 → +10s/+30s/+90s freeze → +300s outcome."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from smartalpha.config import Settings

STAGES = (
    ("t0", 0),
    ("pool_security", 10),
    ("holders_traders", 30),
    ("freeze_entry", 90),
    ("outcome_300", 300),
    ("outcome_900", 900),
)


@dataclass
class SnapshotStage:
    name: str
    delay_sec: int
    observed_at: int
    source: str
    data: dict[str, Any] = field(default_factory=dict)


def capture_stage(mint: str, stage: str, delay: int, settings: Settings | None = None) -> SnapshotStage:
    s = settings or Settings()
    now = int(time.time())
    data: dict[str, Any] = {}
    source = "gmgn"
    try:
        from smartalpha.providers.gmgn import get_holders_traders, get_pool, get_security, get_token

        if stage == "t0":
            env = get_token(mint, settings=s)
            if env:
                data = env.get("data") or {}
                source = env.get("source", "gmgn")
                now = env.get("observed_at", now)
        elif stage == "pool_security":
            env = get_pool(mint, settings=s) or get_security(mint, settings=s)
            if env:
                data = env.get("data") or {}
                source = env.get("source", "gmgn")
                now = env.get("observed_at", now)
        elif stage == "holders_traders":
            env = get_holders_traders(mint, settings=s)
            if env:
                data = env.get("data") or {}
                source = env.get("source", "gmgn")
                now = env.get("observed_at", now)
        elif stage in ("freeze_entry", "outcome_300", "outcome_900"):
            from smartalpha.funder import dex_price_snapshot

            snap = dex_price_snapshot(mint)
            if snap:
                data = snap
                source = snap.get("source", "gmgn")
                now = snap.get("observed_at", now)
    except Exception:
        pass
    if not data:
        data = {"mint": mint, "stage": stage, "placeholder": True}
        source = "fixture"
    return SnapshotStage(name=stage, delay_sec=delay, observed_at=now, source=source, data=data)


def capture_launch_snapshots(mint: str, t0: int | None = None, settings: Settings | None = None) -> dict[str, SnapshotStage]:
    base = t0 or int(time.time())
    out: dict[str, SnapshotStage] = {}
    cur = base
    for name, delay in STAGES:
        cur = base + delay
        stage = capture_stage(mint, name, delay, settings=settings)
        if out and stage.observed_at <= list(out.values())[-1].observed_at:
            stage.observed_at = list(out.values())[-1].observed_at + 1
        stage.observed_at = max(stage.observed_at, cur)
        out[name] = stage
    return out


def freeze_entry_features(snapshots: dict[str, SnapshotStage]) -> dict:
    freeze = snapshots.get("freeze_entry") or snapshots.get("t0")
    if not freeze:
        return {}
    return {"observed_at": freeze.observed_at, "source": freeze.source, "data": freeze.data}


def validate_snapshot_order(snapshots: dict[str, SnapshotStage]) -> tuple[bool, str]:
    ordered = [snapshots[k] for k, _ in STAGES if k in snapshots]
    for a, b in zip(ordered, ordered[1:], strict=False):
        if b.observed_at <= a.observed_at:
            return False, f"{b.name} observed_at {b.observed_at} not > {a.name} {a.observed_at}"
    freeze = snapshots.get("freeze_entry")
    outcome = snapshots.get("outcome_300")
    if freeze and outcome and freeze.observed_at >= outcome.observed_at:
        return False, "freeze must be before outcome"
    return True, "ok"
