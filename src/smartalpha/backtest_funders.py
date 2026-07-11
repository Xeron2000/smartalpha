from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from smartalpha.config import ROOT, Settings, rpc_url
from smartalpha.discover_funders import load_mint_list
from smartalpha.exit_rules import EXIT_MODES, exit_params, sim_exit
from smartalpha.funder import HotFunder, dex_token_outcome
from smartalpha.launch_intel import analyze_launch
from smartalpha.rpc import SolanaRpc
from smartalpha.session_funders import resolve_backtest_hot_funders
from smartalpha.signal_rules import (
    SignalLevel,
    classify_signal,
    hot_organic_buyers,
    should_follow_launch,
    should_follow_launch_balanced,
    should_follow_launch_legacy,
)


@dataclass
class FunderTrade:
    mint: str
    signaled: bool
    hot_funders: list[str]
    hot_organic_buyers: int
    recommendation: str
    copytrap_risk: str
    liquidity_usd: float | None = None
    signal_level: str = "skip"
    gain_h1_pct: float | None = None
    gain_h6_pct: float | None = None
    gain_h24_pct: float | None = None
    pnl_h1_sol: float | None = None
    pnl_h6_sol: float | None = None
    pnl_h24_sol: float | None = None
    pnl_tpsl_sol: float | None = None
    tpsl_exit: str | None = None


@dataclass
class FunderBacktestResult:
    mints_scanned: int
    signals: int
    signals_legacy: int
    skipped: int
    liquidity_filtered: int = 0
    position_sol: float = 0.5
    tp_pct: float = 100.0
    sl_pct: float = 30.0
    exit_mode: str = "hybrid"
    net_h1: float = 0.0
    net_h6: float = 0.0
    net_h24: float = 0.0
    net_tpsl: float = 0.0
    wins_h24: int = 0
    losses_h24: int = 0
    wins_tpsl: int = 0
    losses_tpsl: int = 0
    trades: list[FunderTrade] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ExitCompareResult:
    mints_scanned: int
    signals: int
    skipped: int
    liquidity_filtered: int
    position_sol: float
    modes: dict[str, dict[str, float | int]]
    notes: list[str] = field(default_factory=list)


def load_mints_with_pairs(path: Path) -> list[tuple[str, str | None]]:
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        if "mints_traced" in data:
            pair_map = {c["mint"]: c.get("pair") for c in data.get("candidates") or []}
            return [(m, pair_map.get(m)) for m in data["mints_traced"]]
        if "candidates" in data:
            return [(c["mint"], c.get("pair")) for c in data["candidates"]]
    return [(m, None) for m in load_mint_list(path)]


def _entry_kwargs(s: Settings, liquidity_usd: float | None, pair_age_hours: float | None) -> dict:
    return {
        "min_hot_buyers": s.signal_min_hot_buyers,
        "liquidity_usd": liquidity_usd,
        "min_liquidity_usd": s.signal_min_liquidity_usd,
        "allow_unknown_liq": s.signal_allow_unknown_liq,
        "pair_age_hours": pair_age_hours,
        "ignore_stale_low_liq": s.backtest_ignore_stale_liq,
    }


def _entry_signaled(
    intel,
    *,
    s: Settings,
    balanced: bool,
    legacy: bool,
    liquidity_usd: float | None,
    pair_age_hours: float | None = None,
) -> bool:
    if legacy:
        return should_follow_launch_legacy(intel)
    kw = _entry_kwargs(s, liquidity_usd, pair_age_hours)
    if balanced:
        return should_follow_launch_balanced(intel, **kw)
    return should_follow_launch(intel, **kw)


def _signal_level(
    intel,
    *,
    s: Settings,
    liquidity_usd: float | None,
    pair_age_hours: float | None = None,
) -> SignalLevel:
    return classify_signal(intel, **_entry_kwargs(s, liquidity_usd, pair_age_hours))


def _exit_note(mode: str, ep, tp: float, sl: float) -> str:
    if mode == "fixed":
        return f"exit=fixed tp={tp}% sl={sl}%"
    if mode == "scale":
        return f"exit=scale scale={ep.scale_half_pct}%"
    if mode == "hybrid":
        return (
            f"exit=hybrid early<{ep.early_cut_h1_pct:.0f}% scale@{ep.scale_half_pct:.0f}% "
            f"runner trail/{ep.trail_drawdown_pct:.0f}%"
        )
    if mode == "ladder":
        return (
            f"exit=ladder {ep.ladder_frac1:.0%}@{ep.ladder_tp1_pct:.0f}% "
            f"{ep.ladder_frac2:.0%}@{ep.ladder_tp2_pct:.0f}% trail/{ep.trail_drawdown_pct:.0f}%"
        )
    return f"exit={mode}"


def run_funder_backtest(
    mints: list[tuple[str, str | None]],
    *,
    settings: Settings | None = None,
    position_sol: float = 0.5,
    tp_pct: float | None = None,
    sl_pct: float | None = None,
    exit_mode: str = "hybrid",
    balanced: bool = False,
    legacy: bool = False,
    rpc: SolanaRpc | None = None,
    mints_source: Path | None = None,
    hot_funders: dict[str, HotFunder] | None = None,
) -> FunderBacktestResult:
    s = settings or Settings()
    rpc = rpc or SolanaRpc(rpc_url(s))
    if hot_funders is None:
        hot_funders, session_notes = resolve_backtest_hot_funders(
            mints, rpc, s, source_path=mints_source
        )
    else:
        session_notes = [f"session_hot_funders={len(hot_funders)} (preset)"]
    slip = s.backtest_slippage
    tp = tp_pct if tp_pct is not None else s.backtest_tp_pct
    sl = sl_pct if sl_pct is not None else s.backtest_sl_pct
    ep = exit_params(s)

    res = FunderBacktestResult(
        mints_scanned=0,
        signals=0,
        signals_legacy=0,
        skipped=0,
        position_sol=position_sol,
        tp_pct=tp,
        sl_pct=sl,
        exit_mode=exit_mode,
        notes=[
            "proxy: DexScreener priceChange; not block-0 fill",
            f"slippage={slip*100:.0f}% position={position_sol} SOL",
            f"entry={'legacy' if legacy else 'balanced' if balanced else 'strict'}",
            f"min_liquidity_usd={s.signal_min_liquidity_usd:.0f}",
            f"max_hold_min={ep.max_hold_min} (h1 proxy)",
            _exit_note(exit_mode, ep, tp, sl),
            *session_notes,
        ],
    )

    for mint, pair_hint in mints:
        try:
            intel = analyze_launch(
                mint,
                rpc,
                pair_address=pair_hint,
                max_sigs=35,
                settings=s,
                hot_funders=hot_funders,
            )
        except Exception as exc:
            res.skipped += 1
            res.notes.append(f"skip {mint[:8]}... ({exc})")
            continue

        outcome = dex_token_outcome(mint)
        liq = outcome.get("liquidity_usd") if outcome else None
        age_h = outcome.get("pair_age_hours") if outcome else None
        raw_signal = _entry_signaled(
            intel,
            s=s,
            balanced=balanced,
            legacy=legacy,
            liquidity_usd=liq,
            pair_age_hours=age_h,
        )
        if should_follow_launch_legacy(intel):
            res.signals_legacy += 1

        trade = FunderTrade(
            mint=mint,
            signaled=raw_signal,
            signal_level=_signal_level(
                intel, s=s, liquidity_usd=liq, pair_age_hours=age_h
            ).value,
            hot_funders=intel.hot_funder_hits,
            hot_organic_buyers=len(hot_organic_buyers(intel)),
            recommendation=intel.recommendation,
            copytrap_risk=intel.copytrap_risk,
            liquidity_usd=liq,
        )
        if (
            not legacy
            and liq is not None
            and s.signal_min_liquidity_usd > 0
            and liq < s.signal_min_liquidity_usd
            and intel.hot_funder_hits
            and not (
                s.backtest_ignore_stale_liq
                and age_h is not None
                and age_h >= s.backtest_stale_liq_hours
            )
        ):
            res.liquidity_filtered += 1

        if outcome:
            trade.gain_h1_pct = outcome.get("h1")
            trade.gain_h6_pct = outcome.get("h6")
            trade.gain_h24_pct = outcome.get("h24")
            if raw_signal:
                trade.pnl_h1_sol = _sim_pnl(trade.gain_h1_pct, position_sol, slip)
                trade.pnl_h6_sol = _sim_pnl(trade.gain_h6_pct, position_sol, slip)
                trade.pnl_h24_sol = _sim_pnl(trade.gain_h24_pct, position_sol, slip)
                trade.pnl_tpsl_sol, trade.tpsl_exit = sim_exit(
                    exit_mode, outcome, position_sol, slip, ep, tp_pct=tp, sl_pct=sl
                )
                res.net_h1 += trade.pnl_h1_sol or 0
                res.net_h6 += trade.pnl_h6_sol or 0
                res.net_h24 += trade.pnl_h24_sol or 0
                res.net_tpsl += trade.pnl_tpsl_sol or 0
                if (trade.pnl_h24_sol or 0) >= 0:
                    res.wins_h24 += 1
                else:
                    res.losses_h24 += 1
                if (trade.pnl_tpsl_sol or 0) >= 0:
                    res.wins_tpsl += 1
                else:
                    res.losses_tpsl += 1

        res.mints_scanned += 1
        if raw_signal:
            res.signals += 1
        res.trades.append(trade)
        time.sleep(0.3)

    return res


def run_exit_compare(
    mints: list[tuple[str, str | None]],
    *,
    settings: Settings | None = None,
    position_sol: float = 0.5,
    tp_pct: float | None = None,
    sl_pct: float | None = None,
    balanced: bool = False,
    legacy: bool = False,
    rpc: SolanaRpc | None = None,
    mints_source: Path | None = None,
    hot_funders: dict[str, HotFunder] | None = None,
) -> ExitCompareResult:
    s = settings or Settings()
    rpc = rpc or SolanaRpc(rpc_url(s))
    if hot_funders is None:
        hot_funders, session_notes = resolve_backtest_hot_funders(
            mints, rpc, s, source_path=mints_source
        )
    else:
        session_notes = [f"session_hot_funders={len(hot_funders)} (preset)"]
    slip = s.backtest_slippage
    tp = tp_pct if tp_pct is not None else s.backtest_tp_pct
    sl = sl_pct if sl_pct is not None else s.backtest_sl_pct
    ep = exit_params(s)

    modes: dict[str, dict[str, float | int]] = {
        m: {"net_tpsl_sol": 0.0, "wins": 0, "losses": 0} for m in EXIT_MODES
    }
    res = ExitCompareResult(
        mints_scanned=0,
        signals=0,
        skipped=0,
        liquidity_filtered=0,
        position_sol=position_sol,
        modes=modes,
        notes=[
            "single-pass compare on same entry signals",
            f"min_liquidity_usd={s.signal_min_liquidity_usd:.0f}",
            f"max_hold_min={ep.max_hold_min}",
            *session_notes,
        ],
    )

    for mint, pair_hint in mints:
        try:
            intel = analyze_launch(
                mint,
                rpc,
                pair_address=pair_hint,
                max_sigs=35,
                settings=s,
                hot_funders=hot_funders,
            )
        except Exception as exc:
            res.skipped += 1
            res.notes.append(f"skip {mint[:8]}... ({exc})")
            continue

        outcome = dex_token_outcome(mint)
        liq = outcome.get("liquidity_usd") if outcome else None
        age_h = outcome.get("pair_age_hours") if outcome else None
        signaled = _entry_signaled(
            intel,
            s=s,
            balanced=balanced,
            legacy=legacy,
            liquidity_usd=liq,
            pair_age_hours=age_h,
        )
        if (
            liq is not None
            and s.signal_min_liquidity_usd > 0
            and liq < s.signal_min_liquidity_usd
            and intel.hot_funder_hits
            and not (
                s.backtest_ignore_stale_liq
                and age_h is not None
                and age_h >= s.backtest_stale_liq_hours
            )
        ):
            res.liquidity_filtered += 1

        if signaled and outcome:
            for mode in EXIT_MODES:
                pnl_v, _ = sim_exit(
                    mode, outcome, position_sol, slip, ep, tp_pct=tp, sl_pct=sl
                )
                if pnl_v is None:
                    continue
                modes[mode]["net_tpsl_sol"] = float(modes[mode]["net_tpsl_sol"]) + pnl_v
                if pnl_v >= 0:
                    modes[mode]["wins"] = int(modes[mode]["wins"]) + 1
                else:
                    modes[mode]["losses"] = int(modes[mode]["losses"]) + 1

        res.mints_scanned += 1
        if signaled:
            res.signals += 1
        time.sleep(0.3)

    for mode in res.modes:
        res.modes[mode]["net_tpsl_sol"] = round(float(res.modes[mode]["net_tpsl_sol"]), 4)
    return res


def _sim_pnl(gain_pct: float | None, position: float, slip: float) -> float | None:
    if gain_pct is None:
        return None
    from smartalpha.exit_rules import pnl

    return pnl(gain_pct, position, slip)


def write_funder_backtest_report(result: FunderBacktestResult, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "funder_backtest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    closed_h24 = result.wins_h24 + result.losses_h24
    closed_tpsl = result.wins_tpsl + result.losses_tpsl
    payload = {
        "mints_scanned": result.mints_scanned,
        "signals": result.signals,
        "signals_legacy": result.signals_legacy,
        "skipped": result.skipped,
        "liquidity_filtered": result.liquidity_filtered,
        "position_sol": result.position_sol,
        "tp_pct": result.tp_pct,
        "sl_pct": result.sl_pct,
        "exit_mode": result.exit_mode,
        "net_h1_sol": round(result.net_h1, 4),
        "net_h6_sol": round(result.net_h6, 4),
        "net_h24_sol": round(result.net_h24, 4),
        "net_tpsl_sol": round(result.net_tpsl, 4),
        "wins_h24": result.wins_h24,
        "losses_h24": result.losses_h24,
        "win_rate_h24": round(result.wins_h24 / closed_h24, 3) if closed_h24 else None,
        "wins_tpsl": result.wins_tpsl,
        "losses_tpsl": result.losses_tpsl,
        "win_rate_tpsl": round(result.wins_tpsl / closed_tpsl, 3) if closed_tpsl else None,
        "notes": result.notes,
        "trades": [
            {
                "mint": t.mint,
                "signaled": t.signaled,
                "signal_level": t.signal_level,
                "hot_organic_buyers": t.hot_organic_buyers,
                "hot_funders": t.hot_funders,
                "recommendation": t.recommendation,
                "liquidity_usd": t.liquidity_usd,
                "gain_h24_pct": t.gain_h24_pct,
                "pnl_h24_sol": round(t.pnl_h24_sol, 4) if t.pnl_h24_sol is not None else None,
                "pnl_tpsl_sol": round(t.pnl_tpsl_sol, 4) if t.pnl_tpsl_sol is not None else None,
                "tpsl_exit": t.tpsl_exit,
            }
            for t in result.trades
        ],
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p


def write_exit_compare_report(result: ExitCompareResult, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "exit_compare.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    ranked = sorted(
        result.modes.items(),
        key=lambda kv: float(kv[1]["net_tpsl_sol"]),
        reverse=True,
    )
    payload = {
        "mints_scanned": result.mints_scanned,
        "signals": result.signals,
        "skipped": result.skipped,
        "liquidity_filtered": result.liquidity_filtered,
        "position_sol": result.position_sol,
        "notes": result.notes,
        "ranked": [{"mode": m, **stats} for m, stats in ranked],
        "modes": result.modes,
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p
