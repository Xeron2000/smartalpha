"""30s OHLC Candle Execution Engine —逐根判断 TP/SL/scale/trail，保守 adverse ordering."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Candle:
    time: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


def parse_kline_candles(raw: list[Any]) -> list[Candle]:
    out: list[Candle] = []
    for c in raw:
        if isinstance(c, dict):
            t = c.get("time") or c.get("t") or c.get("timestamp")
            try:
                ts = int(t)
                if ts > 1_000_000_000_0:
                    ts //= 1000
            except Exception:
                continue
            try:
                o = float(c.get("open") or c.get("o") or 0)
                h = float(c.get("high") or c.get("h") or o)
                lo = float(c.get("low") or c.get("l") or o)
                cl = float(c.get("close") or c.get("c") or o)
                vol = c.get("volume") or c.get("v")
                vol_f = float(vol) if vol is not None else None
                out.append(Candle(ts, o, h, lo, cl, vol_f))
            except Exception:
                continue
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            try:
                ts = int(c[0])
                if ts > 1_000_000_000_0:
                    ts //= 1000
                o, h, lo, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                out.append(Candle(ts, o, h, lo, cl, None))
            except Exception:
                continue
    out.sort(key=lambda x: x.time)
    return out


def pnl_for_pct(pct: float, position: float, slippage: float) -> float:
    entry = position * (1 + slippage)
    ex = position * (1 + pct / 100) * (1 - slippage)
    return ex - entry


def simulate_fixed(candles: list[Candle], entry: float, position: float, slippage: float, tp_pct: float = 100, sl_pct: float = 30) -> tuple[float | None, str]:
    if not candles:
        return None, "no_candles"
    for idx, c in enumerate(candles):
        # adverse ordering: check SL first (low), then TP (high)
        low_pct = (c.low - entry) / entry * 100
        high_pct = (c.high - entry) / entry * 100
        if low_pct <= -sl_pct:
            return pnl_for_pct(-sl_pct, position, slippage), f"sl@candle{idx}"
        if high_pct >= tp_pct:
            return pnl_for_pct(tp_pct, position, slippage), f"tp@candle{idx}"
    # hold to last close
    last = candles[-1]
    last_pct = (last.close - entry) / entry * 100
    last_pct = max(min(last_pct, tp_pct), -sl_pct)
    return pnl_for_pct(last_pct, position, slippage), "hold_last"


def simulate_scale_half(candles: list[Candle], entry: float, position: float, slippage: float, scale_pct: float = 100) -> tuple[float | None, str]:
    if not candles:
        return None, "no_candles"
    entry_cost = position * (1 + slippage)
    half = position / 2
    scaled = False
    locked = 0.0
    scale_idx = -1
    for idx, c in enumerate(candles):
        low_pct = (c.low - entry) / entry * 100
        high_pct = (c.high - entry) / entry * 100
        if not scaled:
            if low_pct <= -30:  # hard SL before scale
                return pnl_for_pct(-30, position, slippage), f"sl@candle{idx}"
            if high_pct >= scale_pct:
                # take half at scale
                locked = half * (1 + scale_pct / 100) * (1 - slippage)
                scaled = True
                scale_idx = idx
                # adverse check for runner same candle already handled SL, now runner continues
                continue
        else:
            # runner trails after scale
            pass
    if not scaled:
        last = candles[-1]
        last_pct = (last.close - entry) / entry * 100
        return pnl_for_pct(last_pct, position, slippage), "hold_last"
    # runner held to last close
    last = candles[-1]
    last_pct = (last.close - entry) / entry * 100
    runner = half * (1 + last_pct / 100) * (1 - slippage)
    return locked + runner - entry_cost, f"half@candle{scale_idx}+hold_last"


def simulate_dynamic_trail(candles: list[Candle], entry: float, position: float, slippage: float, activate_pct: float = 50, drawdown_pct: float = 30, hard_sl: float = 30, hard_tp: float = 120) -> tuple[float | None, str]:
    if not candles:
        return None, "no_candles"
    peak = 0.0
    trail_on = False
    for idx, c in enumerate(candles):
        low_pct = (c.low - entry) / entry * 100
        high_pct = (c.high - entry) / entry * 100
        close_pct = (c.close - entry) / entry * 100
        if low_pct <= -hard_sl:
            return pnl_for_pct(-hard_sl, position, slippage), f"sl@candle{idx}"
        peak = max(peak, high_pct)
        if peak >= activate_pct:
            trail_on = True
        if high_pct >= hard_tp:
            return pnl_for_pct(hard_tp, position, slippage), f"tp@candle{idx}"
        if trail_on and close_pct <= peak - drawdown_pct:
            return pnl_for_pct(close_pct, position, slippage), f"trail@candle{idx}"
    last = candles[-1]
    last_pct = (last.close - entry) / entry * 100
    last_pct = max(min(last_pct, hard_tp), -hard_sl)
    return pnl_for_pct(last_pct, position, slippage), "hold_last"


def simulate_hybrid(candles: list[Candle], entry: float, position: float, slippage: float) -> tuple[float | None, str]:
    # hybrid = scale 100% half + runner trail
    if not candles:
        return None, "no_candles"
    entry_cost = position * (1 + slippage)
    half = position / 2
    scaled = False
    locked = 0.0
    peak = 0.0
    trail_on = False
    scale_idx = -1
    for idx, c in enumerate(candles):
        low_pct = (c.low - entry) / entry * 100
        high_pct = (c.high - entry) / entry * 100
        close_pct = (c.close - entry) / entry * 100
        if not scaled:
            if low_pct <= -30:
                return pnl_for_pct(-30, position, slippage), f"sl@candle{idx}"
            if high_pct >= 100:
                locked = half * (1 + 100 / 100) * (1 - slippage)
                scaled = True
                scale_idx = idx
                peak = high_pct
                if peak >= 50:
                    trail_on = True
                continue
        else:
            peak = max(peak, high_pct)
            if peak >= 50:
                trail_on = True
            if trail_on and close_pct <= peak - 30:
                runner = half * (1 + close_pct / 100) * (1 - slippage)
                return locked + runner - entry_cost, f"half@candle{scale_idx}+trail@candle{idx}"
    if not scaled:
        last = candles[-1]
        last_pct = (last.close - entry) / entry * 100
        return pnl_for_pct(last_pct, position, slippage), "hold_last"
    last = candles[-1]
    last_pct = (last.close - entry) / entry * 100
    runner = half * (1 + last_pct / 100) * (1 - slippage)
    return locked + runner - entry_cost, f"half@candle{scale_idx}+hold_last"
