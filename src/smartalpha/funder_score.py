"""Funder quality scoring.

Important: DexScreener rolling h24 is a *live* window, not launch-relative return.
For mints discovered days ago as pumps, live h24 is usually negative after dump —
that previously graded every funder as watch/skip even when they funded real winners.

Quality priority:
  1) discovery_gain_pct (from auto-discover / GMGN at selection time)
  2) age-aware live proxy (young pairs → rolling change; old pairs → survival/liq only)
"""

from __future__ import annotations

import statistics
import time
from enum import StrEnum

from smartalpha.funder import dex_pair_meta


class FunderGrade(StrEnum):
    STRONG = "strong"
    MEDIUM = "medium"
    WATCH = "watch"
    SKIP = "skip"


# Win threshold on discovery-time gain (mint was a real runner when selected)
DISCOVERY_WIN_PCT = 100.0  # +100% at discovery counts as win
# Live young-pair win threshold
LIVE_WIN_PCT = 0.0


def shrinked_win_rate(wins: int, trades: int) -> float:
    """Beta(1,1) shrinkage: (wins + 1) / (trades + 2)."""
    return (wins + 1) / (trades + 2)


def grade_rank(value: str | FunderGrade | None) -> int:
    order = {
        FunderGrade.SKIP.value: 0,
        FunderGrade.WATCH.value: 1,
        FunderGrade.MEDIUM.value: 2,
        FunderGrade.STRONG.value: 3,
    }
    if isinstance(value, FunderGrade):
        return order[value.value]
    return order.get(value or "", order[FunderGrade.WATCH.value])


def classify_funder_grade(quality: dict) -> FunderGrade:
    """Grade from shrinked win rate, median return, rug rate, sample size."""
    mint_outcomes = quality.get("mint_outcomes", 0) or 0
    wr_raw = quality.get("win_rate") or 0
    med = quality.get("median_return_pct")
    if med is None:
        med = quality.get("median_h24_pct")  # backward compat
    rug = quality.get("rug_rate") or 0
    source = quality.get("score_source", "live")

    if mint_outcomes < 2:
        return FunderGrade.WATCH

    # Clear noise — only skip on extreme rug or catastrophic median from live path
    if rug >= 0.8:
        return FunderGrade.SKIP
    if source == "live" and med is not None and med <= -80:
        return FunderGrade.SKIP

    wins = max(0, round(wr_raw * mint_outcomes))
    swr = shrinked_win_rate(wins, mint_outcomes)

    # Discovery-backed grades can trust large positive medians
    if (
        mint_outcomes >= 3
        and swr >= 0.55
        and (med is not None and med > 50)
        and rug < 0.35
    ):
        return FunderGrade.STRONG

    if (
        mint_outcomes >= 2
        and swr >= 0.45
        and (med is None or med > -20 or source == "discovery")
        and rug < 0.55
    ):
        # discovery: if mostly wins by construction of pumped set, medium+
        if source == "discovery" and swr >= 0.5 and (med is None or med > 0):
            if mint_outcomes >= 3 and swr >= 0.6 and (med or 0) > 100:
                return FunderGrade.STRONG
            return FunderGrade.MEDIUM
        if source != "discovery" and med is not None and med > -30 and swr >= 0.5:
            return FunderGrade.MEDIUM
        if source == "discovery":
            return FunderGrade.MEDIUM

    # Partial credit: funded ≥2 runners (discovery) even if live survival weak
    discovery_wins = quality.get("discovery_wins") or 0
    if discovery_wins >= 2 and rug < 0.6:
        return FunderGrade.MEDIUM

    return FunderGrade.WATCH


def resolve_mint_quality_point(
    mint: str,
    *,
    discovery_gain_pct: float | None = None,
    rug_liq_usd: float = 5_000.0,
    fetch_live: bool = True,
    sleep: float = 0.0,
) -> dict | None:
    """One mint → return/win/rug flags for funder scoring.

    Returns None if no usable signal at all.
    """
    now = time.time()
    live = dex_pair_meta(mint) if fetch_live else None
    if sleep and fetch_live:
        time.sleep(sleep)

    liq = live.get("liquidity_usd") if live else None
    price = live.get("price_usd") if live else None
    created_ms = live.get("pair_created_at_ms") if live else None
    age_h = None
    if created_ms:
        age_h = max(0.0, (now - created_ms / 1000) / 3600)

    # Rug: dead liquidity (when we can observe it)
    is_rug = liq is not None and liq < rug_liq_usd and (age_h is None or age_h > 1)

    # Primary return: discovery-time pump magnitude
    if discovery_gain_pct is not None:
        ret = float(discovery_gain_pct)
        win = ret >= DISCOVERY_WIN_PCT
        return {
            "mint": mint,
            "return_pct": ret,
            "win": win,
            "rug": is_rug,
            "source": "discovery",
            "liquidity_usd": liq,
            "pair_age_hours": age_h,
        }

    if not live:
        return None

    # Age-aware live proxy
    g24 = live.get("gain_h24_pct")
    g6 = live.get("gain_h6_pct")
    g1 = live.get("gain_h1_pct")

    if age_h is not None and age_h <= 6:
        # Brand new: prefer shorter windows
        ret = g1 if g1 is not None else (g6 if g6 is not None else g24)
        source = "live_young"
    elif age_h is not None and age_h <= 48:
        ret = g24 if g24 is not None else g6
        source = "live_day"
    else:
        # Stale chart: rolling h24 is post-dump noise. Score survival only.
        # Win if still has meaningful liquidity (not rugged to zero).
        if liq is not None and liq >= rug_liq_usd:
            ret = 0.0  # neutral — survived, not a measured pump
            win = True
            source = "live_survival"
            return {
                "mint": mint,
                "return_pct": ret,
                "win": win and not is_rug,
                "rug": is_rug,
                "source": source,
                "liquidity_usd": liq,
                "pair_age_hours": age_h,
            }
        # Dead + old → rug / loss
        ret = g24 if g24 is not None else -99.0
        source = "live_stale"
        return {
            "mint": mint,
            "return_pct": float(ret),
            "win": False,
            "rug": True,
            "source": source,
            "liquidity_usd": liq,
            "pair_age_hours": age_h,
        }

    if ret is None:
        if price is None and liq is None:
            return None
        ret = 0.0

    win = float(ret) > LIVE_WIN_PCT and not is_rug
    if is_rug:
        win = False

    return {
        "mint": mint,
        "return_pct": float(ret),
        "win": win,
        "rug": is_rug,
        "source": source,
        "liquidity_usd": liq,
        "pair_age_hours": age_h,
    }


def score_funder_mints(
    mints: list[str],
    *,
    mint_gains: dict[str, float] | None = None,
    rug_liq_usd: float = 5_000.0,
    rug_gain_h24_pct: float = -90.0,  # kept for API compat; unused in new path
    sleep: float = 0.2,
    fetch_live: bool = True,
) -> dict:
    """Score funder across mints it funded.

    Prefer discovery gains (mint_gains) so quality reflects "funded runners",
    not "live chart still green days later".
    """
    mint_gains = mint_gains or {}
    returns: list[float] = []
    wins = 0
    rugs = 0
    discovery_wins = 0
    sources: list[str] = []
    points = 0

    for mint in mints:
        pt = resolve_mint_quality_point(
            mint,
            discovery_gain_pct=mint_gains.get(mint),
            rug_liq_usd=rug_liq_usd,
            fetch_live=fetch_live,
            sleep=sleep if mint not in mint_gains else 0.0,
        )
        # discovery-only path without live fetch
        if pt is None and mint in mint_gains:
            g = float(mint_gains[mint])
            pt = {
                "mint": mint,
                "return_pct": g,
                "win": g >= DISCOVERY_WIN_PCT,
                "rug": False,
                "source": "discovery",
            }
        if not pt:
            continue
        points += 1
        returns.append(float(pt["return_pct"]))
        sources.append(pt["source"])
        if pt["win"]:
            wins += 1
            if pt["source"] == "discovery":
                discovery_wins += 1
        if pt["rug"]:
            rugs += 1

    n = points
    med = statistics.median(returns) if returns else None
    wr_raw = round(wins / n, 3) if n else 0
    swr = round(shrinked_win_rate(wins, n), 3) if n else 0
    # Dominant source label
    if sources and all(s == "discovery" for s in sources):
        score_source = "discovery"
    elif sources and any(s == "discovery" for s in sources):
        score_source = "mixed"
    else:
        score_source = "live"

    quality = {
        "mint_outcomes": n,
        "win_rate": wr_raw,
        "shrinked_win_rate": swr,
        "median_return_pct": round(med, 2) if med is not None else None,
        # backward-compat key used by older reports / prove
        "median_h24_pct": round(med, 2) if med is not None else None,
        "rug_rate": round(rugs / n, 3) if n else None,
        "discovery_wins": discovery_wins,
        "score_source": score_source,
        "sources": sources[:12],
    }
    grade = classify_funder_grade(quality)
    quality["grade"] = grade.value
    return quality


def enrich_funder_scores(
    recommended: list[dict],
    *,
    mint_gains: dict[str, float] | None = None,
    sleep: float = 0.2,
    fetch_live: bool = True,
) -> list[dict]:
    out: list[dict] = []
    for row in recommended:
        mints = row.get("mints") or []
        quality = score_funder_mints(
            mints,
            mint_gains=mint_gains,
            sleep=sleep,
            fetch_live=fetch_live,
        )
        merged = {**row, "quality": quality}
        merged["weight"] = _adjusted_weight(row.get("weight", 1.0), quality)
        out.append(merged)
    return out


def filter_funders_by_grade(
    funders: list[dict],
    *,
    min_grade: FunderGrade = FunderGrade.MEDIUM,
) -> list[dict]:
    """Return only funders meeting the minimum grade threshold."""
    floor = grade_rank(min_grade)
    return [
        f
        for f in funders
        if grade_rank(f.get("quality", {}).get("grade", "watch")) >= floor
    ]


def mint_gains_from_candidates(candidates: list[dict] | list) -> dict[str, float]:
    """Build mint → discovery gain map from auto_discover candidates."""
    out: dict[str, float] = {}
    for c in candidates or []:
        if isinstance(c, dict):
            mint = c.get("mint")
            gain = c.get("gain_h24_pct")
        else:
            mint = getattr(c, "mint", None)
            gain = getattr(c, "gain_h24_pct", None)
        if mint and gain is not None:
            try:
                out[str(mint)] = float(gain)
            except (TypeError, ValueError):
                continue
    return out


def mint_gains_from_report(path_or_data) -> dict[str, float]:
    """Load discovery gains from auto_discover JSON path or dict."""
    import json
    from pathlib import Path

    if isinstance(path_or_data, dict):
        return mint_gains_from_candidates(path_or_data.get("candidates") or [])
    p = Path(path_or_data)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    return mint_gains_from_candidates(data.get("candidates") or [])


def _adjusted_weight(base: float, quality: dict) -> float:
    """Down-weight bad quality, up-weight exceptional."""
    w = float(base)
    wr = quality.get("shrinked_win_rate") or quality.get("win_rate") or 0
    rr = quality.get("rug_rate") or 0
    med = quality.get("median_return_pct")
    if med is None:
        med = quality.get("median_h24_pct")

    if wr < 0.4:
        w *= 0.7
    if rr > 0.5:
        w *= 0.5
    if med is not None and med > 100 and wr > 0.5:
        w = min(w * 1.2, 2.5)
    if quality.get("score_source") == "discovery" and wr >= 0.6:
        w = min(w * 1.1, 2.5)
    return round(w, 3)
