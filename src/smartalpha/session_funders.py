from __future__ import annotations

import json
import time
from pathlib import Path

from smartalpha.auto_discover import run_auto_discover, write_auto_discover_report
from smartalpha.config import ROOT, Settings
from smartalpha.discover_funders import KNOWN_CEX_FUNDERS, discover_funders
from smartalpha.funder import HotFunder
from smartalpha.funder_score import (
    FunderGrade,
    enrich_funder_scores,
    grade_rank,
    mint_gains_from_report,
)
from smartalpha.rpc import SolanaRpc


def load_session_hot_funders_from_disk(
    settings: Settings | None = None,
    *,
    report_path: Path | None = None,
    min_grade: str | None = None,
) -> tuple[dict[str, HotFunder], list[str], Path]:
    """Load hot funders from existing auto_discover JSON (no RPC rediscover)."""
    s = settings or Settings()
    path = report_path or session_report_path()
    if not path.exists():
        return {}, [f"no session report at {path}"], path
    data = json.loads(path.read_text())
    recommended = data.get("recommended_funders") or []
    if not recommended:
        return {}, [f"empty recommended_funders in {path.name}"], path
    gains = mint_gains_from_report(data)
    has_quality = all((r.get("quality") or {}).get("grade") for r in recommended)
    hot_map, notes = build_hot_funders_from_recommended(
        recommended,
        s,
        min_grade=min_grade,
        mint_gains=gains,
        enrich=not has_quality,
    )
    age = int(time.time() - path.stat().st_mtime)
    notes.insert(0, f"loaded disk {path.name} age={age}s funders={len(hot_map)}")
    if gains:
        notes.append(f"discovery_gains={len(gains)}")
    return hot_map, notes, path


def refresh_session_hot_funders(
    settings: Settings | None = None,
    *,
    min_gain_pct: float | None = None,
    mint_limit: int | None = None,
    min_mint_hits: int | None = None,
    max_buyers: int | None = None,
    min_grade: str | None = None,
    report_path: Path | None = None,
    prefer_cache: bool = False,
    max_cache_age_sec: int | None = None,
) -> tuple[dict[str, HotFunder], list[str], Path]:
    """Build hot funder map.

    prefer_cache=True: use data/auto_discover.json if fresh enough (watch startup).
    prefer_cache=False: full auto-discover (periodic refresh / explicit).
    """
    s = settings or Settings()
    out_path = report_path or session_report_path()
    cache_ttl = (
        max_cache_age_sec
        if max_cache_age_sec is not None
        else max(s.session_refresh_sec, 3600)
    )

    if prefer_cache:
        hot_map, notes, path = load_session_hot_funders_from_disk(
            s, report_path=out_path, min_grade=min_grade
        )
        if hot_map:
            age = int(time.time() - path.stat().st_mtime) if path.exists() else 10**9
            if age <= cache_ttl:
                notes.append(f"cache hit ttl={cache_ttl}s")
                notes.append(f"refreshed_at={int(time.time())}")
                return hot_map, notes, path
            notes.append(f"cache stale age={age}s > ttl={cache_ttl}s — rediscovering")

    report = run_auto_discover(
        s,
        min_gain_pct=min_gain_pct if min_gain_pct is not None else s.session_min_gain_pct,
        mint_limit=mint_limit if mint_limit is not None else s.session_mint_limit,
        min_mint_hits=min_mint_hits if min_mint_hits is not None else s.session_min_mint_hits,
        max_buyers=max_buyers if max_buyers is not None else s.session_max_buyers,
    )
    out_path = write_auto_discover_report(report, path=out_path)
    gains = mint_gains_from_report(out_path)
    hot_map, notes = build_hot_funders_from_recommended(
        getattr(report.discover, "recommended", []),
        s,
        min_grade=min_grade,
        mint_gains=gains,
        enrich=False,  # auto_discover already attached discovery quality
    )
    notes = list(report.notes) + notes
    notes.append(f"refreshed_at={int(time.time())}")
    notes.append("source=live_auto_discover")
    return hot_map, notes, out_path


def build_hot_funders_from_recommended(
    recommended: list[dict],
    settings: Settings | None = None,
    *,
    min_grade: str | None = None,
    enrich: bool = True,
    mint_gains: dict[str, float] | None = None,
) -> tuple[dict[str, HotFunder], list[str]]:
    s = settings or Settings()
    if enrich:
        rows = enrich_funder_scores(
            recommended,
            mint_gains=mint_gains,
            sleep=0.12,
            # If we have discovery gains, skip live re-fetch (faster + correct)
            fetch_live=not bool(mint_gains),
        )
    else:
        rows = list(recommended)
    grade_floor = _parse_grade(min_grade or s.session_min_grade)
    filtered = [
        row
        for row in rows
        if grade_rank(row.get("quality", {}).get("grade")) >= grade_rank(grade_floor)
        and row.get("address") not in KNOWN_CEX_FUNDERS
    ]
    hot_map = {
        row["address"]: HotFunder(
            row["address"],
            row.get("label", ""),
            float(row.get("weight", 1.0)),
        )
        for row in filtered
    }
    notes = [
        "session funders "
        f"{len(hot_map)}/{len(rows)} "
        f"(min_grade={grade_floor.value})"
    ]
    return hot_map, notes


def build_hot_funders_from_mints(
    mints: list[str],
    rpc: SolanaRpc,
    settings: Settings | None = None,
    *,
    min_mint_hits: int | None = None,
    max_buyers: int | None = None,
    min_grade: str | None = None,
) -> tuple[dict[str, HotFunder], list[str]]:
    s = settings or Settings()
    report = discover_funders(
        mints,
        rpc,
        max_buyers=max_buyers if max_buyers is not None else s.session_max_buyers,
        min_mint_hits=min_mint_hits if min_mint_hits is not None else s.session_min_mint_hits,
        settings=s,
    )
    return build_hot_funders_from_recommended(report.recommended, s, min_grade=min_grade)


def resolve_backtest_hot_funders(
    mints: list[tuple[str, str | None]],
    rpc: SolanaRpc,
    settings: Settings | None = None,
    *,
    source_path: Path | None = None,
) -> tuple[dict[str, HotFunder], list[str]]:
    """Prefer recommended_funders from report; else discover from mint batch."""
    s = settings or Settings()
    if source_path and source_path.exists() and source_path.suffix == ".json":
        data = json.loads(source_path.read_text())
        recommended = data.get("recommended_funders") or []
        if recommended:
            gains = mint_gains_from_report(data)
            hot_map, notes = build_hot_funders_from_recommended(
                recommended, s, mint_gains=gains
            )
            notes.insert(0, f"session source={source_path.name}")
            if gains:
                notes.append(f"discovery_gains={len(gains)}")
            return hot_map, notes

    mint_list = [m for m, _ in mints]
    hot_map, notes = build_hot_funders_from_mints(mint_list, rpc, s)
    notes.insert(0, f"session source=discover({len(mint_list)} mints)")
    return hot_map, notes


def session_report_path() -> Path:
    return ROOT / "data" / "auto_discover.json"


def _parse_grade(value: str) -> FunderGrade:
    try:
        return FunderGrade(value)
    except ValueError:
        return FunderGrade.MEDIUM


def _grade_rank(value: str | None) -> int:
    """Backward-compat wrapper — prefer grade_rank from funder_score."""
    return grade_rank(value)
