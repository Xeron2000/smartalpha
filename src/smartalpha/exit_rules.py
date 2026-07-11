from __future__ import annotations

from dataclasses import dataclass

from smartalpha.config import Settings

EXIT_MODES = ("fixed", "dynamic", "scale", "hybrid", "ladder")


@dataclass(frozen=True)
class ExitParams:
    hard_sl_pct: float = 30.0
    hard_tp_pct: float = 120.0
    trail_activate_pct: float = 50.0
    trail_drawdown_pct: float = 30.0
    early_cut_h1_pct: float = 0.0
    stall_h6_pct: float = 15.0
    scale_half_pct: float = 100.0
    max_hold_min: int = 30
    ladder_tp1_pct: float = 100.0
    ladder_tp2_pct: float = 200.0
    ladder_frac1: float = 0.25
    ladder_frac2: float = 0.25


def exit_params(settings: Settings | None = None) -> ExitParams:
    s = settings or Settings()
    return ExitParams(
        hard_sl_pct=s.backtest_sl_pct,
        hard_tp_pct=s.backtest_hard_tp_pct,
        trail_activate_pct=s.backtest_trail_activate_pct,
        trail_drawdown_pct=s.backtest_trail_drawdown_pct,
        early_cut_h1_pct=s.backtest_early_cut_h1_pct,
        stall_h6_pct=s.backtest_stall_h6_pct,
        scale_half_pct=s.backtest_scale_half_pct,
        max_hold_min=s.backtest_max_hold_min,
        ladder_tp1_pct=s.backtest_ladder_tp1_pct,
        ladder_tp2_pct=s.backtest_ladder_tp2_pct,
    )


def sim_exit(
    mode: str,
    gains: dict[str, float],
    position: float,
    slip: float,
    params: ExitParams | None = None,
    *,
    tp_pct: float = 100.0,
    sl_pct: float = 30.0,
) -> tuple[float | None, str | None]:
    p = params or ExitParams()
    if mode == "fixed":
        return sim_fixed_tp_sl(gains, tp_pct, sl_pct, position, slip)
    if mode == "dynamic":
        return sim_dynamic_exit(gains, position, slip, p)
    if mode == "scale":
        return sim_scale_half(gains, position, slip, p.scale_half_pct)
    if mode == "hybrid":
        return sim_hybrid_exit(gains, position, slip, p)
    if mode == "ladder":
        return sim_ladder_exit(gains, position, slip, p)
    raise ValueError(f"unknown exit mode: {mode}")


def sim_fixed_tp_sl(
    gains: dict[str, float],
    tp_pct: float,
    sl_pct: float,
    position: float,
    slip: float,
) -> tuple[float | None, str | None]:
    for key in ("h1", "h6", "h24"):
        g = gains.get(key)
        if g is None:
            continue
        if g >= tp_pct:
            return pnl(tp_pct, position, slip), f"tp@{key}"
        if g <= -sl_pct:
            return pnl(-sl_pct, position, slip), f"sl@{key}"
    g24 = gains.get("h24")
    if g24 is None:
        return None, None
    capped = max(min(g24, tp_pct), -sl_pct)
    return pnl(capped, position, slip), "h24"


def sim_dynamic_exit(
    gains: dict[str, float],
    position: float,
    slip: float,
    params: ExitParams | None = None,
) -> tuple[float | None, str | None]:
    p = params or ExitParams()
    peak = 0.0
    trail_on = False

    for key in ("h1", "h6", "h24"):
        g = gains.get(key)
        if g is None:
            continue
        if g <= -p.hard_sl_pct:
            return pnl(-p.hard_sl_pct, position, slip), f"sl@{key}"
        if key == "h1" and g < p.early_cut_h1_pct:
            return pnl(g, position, slip), f"early@{key}"
        if _time_stall_exit(key, g, p, partial=False):
            return pnl(g, position, slip), f"time@{key}"
        peak = max(peak, g)
        if peak >= p.trail_activate_pct:
            trail_on = True
        if g >= p.hard_tp_pct:
            return pnl(p.hard_tp_pct, position, slip), f"tp@{key}"
        if trail_on and g <= peak - p.trail_drawdown_pct:
            return pnl(g, position, slip), f"trail@{key}"
        if key == "h6" and not trail_on and g < p.stall_h6_pct:
            return pnl(g, position, slip), f"stall@{key}"

    g24 = gains.get("h24")
    if g24 is None:
        return None, None
    if trail_on and g24 <= peak - p.trail_drawdown_pct:
        return pnl(g24, position, slip), "trail@h24"
    capped = max(min(g24, p.hard_tp_pct), -p.hard_sl_pct)
    return pnl(capped, position, slip), "h24"


def sim_scale_half(
    gains: dict[str, float],
    position: float,
    slip: float,
    scale_pct: float = 100.0,
) -> tuple[float | None, str | None]:
    g24 = gains.get("h24")
    if g24 is None:
        return None, None

    scale_key: str | None = None
    for key in ("h1", "h6", "h24"):
        g = gains.get(key)
        if g is not None and g >= scale_pct:
            scale_key = key
            break

    entry = position * (1 + slip)
    half = position / 2

    if scale_key is None:
        return pnl(g24, position, slip), "h24"

    exit_first = half * (1 + scale_pct / 100) * (1 - slip)
    exit_rest = half * (1 + g24 / 100) * (1 - slip)
    return exit_first + exit_rest - entry, f"half@{scale_key}+h24"


def sim_hybrid_exit(
    gains: dict[str, float],
    position: float,
    slip: float,
    params: ExitParams | None = None,
) -> tuple[float | None, str | None]:
    p = params or ExitParams()
    if gains.get("h24") is None:
        return None, None

    entry = position * (1 + slip)
    half = position / 2
    scaled = False
    scale_key: str | None = None
    locked = 0.0
    runner_peak = 0.0
    runner_trail = False

    for key in ("h1", "h6", "h24"):
        g = gains.get(key)
        if g is None:
            continue

        if not scaled:
            if g <= -p.hard_sl_pct:
                return pnl(-p.hard_sl_pct, position, slip), f"sl@{key}"
            if key == "h1" and g < p.early_cut_h1_pct and g < p.scale_half_pct:
                return pnl(g, position, slip), f"early@{key}"
            if _time_stall_exit(key, g, p, partial=False) and g < p.scale_half_pct:
                return pnl(g, position, slip), f"time@{key}"
            if key == "h6" and g < p.stall_h6_pct and g < p.scale_half_pct:
                return pnl(g, position, slip), f"stall@{key}"
            if g >= p.scale_half_pct:
                locked = _leg_exit(p.scale_half_pct, half, slip)
                scaled = True
                scale_key = key
                runner_peak = g
                if runner_peak >= p.trail_activate_pct:
                    runner_trail = True
                if runner_trail and g <= runner_peak - p.trail_drawdown_pct:
                    return locked + _leg_exit(g, half, slip) - entry, f"half@{key}+trail@{key}"
                if key == "h24":
                    return locked + _leg_exit(g, half, slip) - entry, f"half@{key}+h24"
                continue

        runner_peak = max(runner_peak, g)
        if runner_peak >= p.trail_activate_pct:
            runner_trail = True
        if runner_trail and g <= runner_peak - p.trail_drawdown_pct:
            return locked + _leg_exit(g, half, slip) - entry, f"half@{scale_key}+trail@{key}"
        if key == "h24":
            return locked + _leg_exit(g, half, slip) - entry, f"half@{scale_key}+h24"

    if not scaled:
        return pnl(gains["h24"], position, slip), "h24"
    return None, None


def sim_ladder_exit(
    gains: dict[str, float],
    position: float,
    slip: float,
    params: ExitParams | None = None,
) -> tuple[float | None, str | None]:
    """25% @ 2x, 25% @ 3x, runner trails (industry TP ladder)."""
    p = params or ExitParams()
    if gains.get("h24") is None:
        return None, None

    entry = position * (1 + slip)
    remaining = position
    locked = 0.0
    tags: list[str] = []
    runner_peak = 0.0
    runner_trail = False
    ladder = (
        (p.ladder_frac1, p.ladder_tp1_pct, "tp1"),
        (p.ladder_frac2, p.ladder_tp2_pct, "tp2"),
    )
    filled: set[str] = set()

    for key in ("h1", "h6", "h24"):
        g = gains.get(key)
        if g is None:
            continue

        if remaining >= position * 0.99:
            if g <= -p.hard_sl_pct:
                return pnl(-p.hard_sl_pct, position, slip), f"sl@{key}"
            if key == "h1" and g < p.early_cut_h1_pct:
                return pnl(g, position, slip), f"early@{key}"
            if _time_stall_exit(key, g, p, partial=False):
                return pnl(g, position, slip), f"time@{key}"
            if key == "h6" and g < p.stall_h6_pct:
                return pnl(g, position, slip), f"stall@{key}"

        for frac, tp_pct, tag in ladder:
            if tag in filled or g < tp_pct:
                continue
            sell = min(position * frac, remaining)
            locked += _leg_exit(tp_pct, sell, slip)
            remaining -= sell
            filled.add(tag)
            tags.append(f"{tag}@{key}")

        if remaining <= 0:
            return locked - entry, "+".join(tags)

        runner_peak = max(runner_peak, g)
        if runner_peak >= p.trail_activate_pct:
            runner_trail = True
        if runner_trail and filled and g <= runner_peak - p.trail_drawdown_pct:
            locked += _leg_exit(g, remaining, slip)
            return locked - entry, "+".join(tags) + f"+trail@{key}"

    g24 = gains["h24"]
    if remaining > 0:
        locked += _leg_exit(g24, remaining, slip)
    if not tags and remaining >= position * 0.99:
        return pnl(g24, position, slip), "h24"
    suffix = "h24" if remaining > 0 else ""
    reason = "+".join(tags + ([suffix] if suffix else []))
    return locked - entry, reason or "h24"


def _time_stall_exit(key: str, gain: float, params: ExitParams, *, partial: bool) -> bool:
    # ponytail: h1 proxies max_hold_min (DexScreener has no 30m bucket)
    if partial or params.max_hold_min <= 0 or key != "h1":
        return False
    return gain < params.stall_h6_pct


def _leg_exit(gain_pct: float, leg_size: float, slip: float) -> float:
    return leg_size * (1 + gain_pct / 100) * (1 - slip)


def pnl(gain_pct: float, position: float, slip: float) -> float:
    entry = position * (1 + slip)
    exit_val = position * (1 + gain_pct / 100) * (1 - slip)
    return exit_val - entry
