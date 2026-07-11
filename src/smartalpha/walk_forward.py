from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from smartalpha.config import ROOT, Settings, rpc_url
from smartalpha.discover_funders import discover_funders
from smartalpha.funder import HotFunder, dex_pair_created_at
from smartalpha.funder_score import enrich_funder_scores, mint_gains_from_report
from smartalpha.rpc import SolanaRpc


@dataclass
class WalkForwardResult:
    split_mode: str
    train_ratio: float
    train_days: int
    test_days: int
    anchor_ts: int
    train_window: tuple[int, int]
    test_window: tuple[int, int]
    train_mints: list[str]
    test_mints: list[str]
    skipped_no_ts: list[str]
    train_funders: list[dict]
    test_compare: dict | None
    notes: list[str] = field(default_factory=list)


def split_mints_chronological(
    mint_times: dict[str, int],
    *,
    train_ratio: float = 0.7,
) -> tuple[list[str], list[str], tuple[int, int], tuple[int, int]]:
    """Older mints → train, newer → test (for historical batch OOS)."""
    ordered = sorted(mint_times.items(), key=lambda x: x[1])
    n = len(ordered)
    if n < 2:
        m = [ordered[0][0]] if n == 1 else []
        return m, [], (0, 0), (0, 0)
    cut = max(1, min(n - 1, int(n * train_ratio)))
    train = [m for m, _ in ordered[:cut]]
    test = [m for m, _ in ordered[cut:]]
    train_win = (ordered[0][1], ordered[cut - 1][1])
    test_win = (ordered[cut][1], ordered[-1][1])
    return train, test, train_win, test_win


def split_mints_by_window(
    mint_times: dict[str, int],
    *,
    train_days: int,
    test_days: int,
    anchor_ts: int | None = None,
) -> tuple[list[str], list[str], tuple[int, int], tuple[int, int]]:
    anchor = anchor_ts or int(time.time())
    test_start = anchor - test_days * 86400
    train_end = test_start
    train_start = train_end - train_days * 86400
    train: list[str] = []
    test: list[str] = []
    for mint, ts in mint_times.items():
        if train_start <= ts < train_end:
            train.append(mint)
        elif test_start <= ts < anchor:
            test.append(mint)
    return train, test, (train_start, train_end), (test_start, anchor)


def resolve_mint_times(mints: list[str], *, sleep: float = 0.25) -> tuple[dict[str, int], list[str]]:
    times: dict[str, int] = {}
    skipped: list[str] = []
    for mint in mints:
        ts = dex_pair_created_at(mint)
        if ts:
            times[mint] = ts
        else:
            skipped.append(mint)
        time.sleep(sleep)
    return times, skipped


def run_walk_forward(
    mints: list[tuple[str, str | None]],
    *,
    settings: Settings | None = None,
    split_mode: str = "chronological",
    train_ratio: float = 0.7,
    train_days: int = 7,
    test_days: int = 7,
    min_mint_hits: int = 2,
    position_sol: float = 0.5,
    mint_gains: dict[str, float] | None = None,
    mints_source: Path | None = None,
) -> WalkForwardResult:
    s = settings or Settings()
    rpc = SolanaRpc(rpc_url(s))
    mint_list = [m for m, _ in mints]
    pair_map = {m: p for m, p in mints}
    gains = dict(mint_gains or {})
    if mints_source:
        gains = {**mint_gains_from_report(mints_source), **gains}

    mint_times, skipped = resolve_mint_times(mint_list)
    if split_mode == "calendar":
        train_mints, test_mints, train_win, test_win = split_mints_by_window(
            mint_times, train_days=train_days, test_days=test_days
        )
        notes: list[str] = [
            f"split=calendar train_days={train_days} test_days={test_days}",
            "train funders from train window only; test uses train funders",
        ]
    else:
        train_mints, test_mints, train_win, test_win = split_mints_chronological(
            mint_times, train_ratio=train_ratio
        )
        notes = [
            f"split=chronological train_ratio={train_ratio}",
            "older pairCreatedAt → train, newer → test (OOS on historical batch)",
        ]

    notes.append("pairCreatedAt proxy from DexScreener")
    if not train_mints:
        notes.append("no train mints in window — widen train-days or add mints")
    if not test_mints:
        notes.append("no test mints in window — widen test-days or add mints")

    train_funders: list[dict] = []
    if train_mints:
        dr = discover_funders(
            train_mints,
            rpc,
            min_mint_hits=min_mint_hits,
            settings=s,
        )
        # Only use discovery gains for train mints (avoid test leakage of labels)
        train_gains = {m: gains[m] for m in train_mints if m in gains}
        train_funders = enrich_funder_scores(
            dr.recommended,
            mint_gains=train_gains,
            sleep=0.1 if not train_gains else 0.0,
            # Discovery gains = true "funded a runner" label; skip live rolling h24
            fetch_live=not bool(train_gains),
        )
        notes.append(
            f"train discover: {len(train_funders)} funders from {len(train_mints)} mints "
            f"(discovery_gains={len(train_gains)})"
        )

    from smartalpha.discover_funders import KNOWN_CEX_FUNDERS

    hot_map = {
        r["address"]: HotFunder(r["address"], r.get("label", ""), float(r.get("weight", 1.0)))
        for r in train_funders
        if r.get("address") not in KNOWN_CEX_FUNDERS
    }
    if len(hot_map) < len(train_funders):
        notes.append(
            f"dropped {len(train_funders) - len(hot_map)} CEX/hot wallets from train funders"
        )

    test_compare = None
    if test_mints and hot_map:
        test_pairs = [(m, pair_map.get(m)) for m in test_mints]
        cmp = _run_compare_with_funders(
            test_pairs, hot_map, settings=s, position_sol=position_sol
        )
        test_compare = {
            "signals": cmp.signals,
            "liquidity_filtered": cmp.liquidity_filtered,
            "modes": cmp.modes,
            "notes": cmp.notes,
        }
    elif test_mints and not hot_map:
        notes.append("test skipped: no train funders discovered")

    return WalkForwardResult(
        split_mode=split_mode,
        train_ratio=train_ratio,
        train_days=train_days,
        test_days=test_days,
        anchor_ts=int(time.time()),
        train_window=train_win,
        test_window=test_win,
        train_mints=train_mints,
        test_mints=test_mints,
        skipped_no_ts=skipped,
        train_funders=train_funders,
        test_compare=test_compare,
        notes=notes,
    )


def _run_compare_with_funders(
    mints: list[tuple[str, str | None]],
    hot_funders: dict[str, HotFunder],
    *,
    settings: Settings,
    position_sol: float,
):
    """Like run_exit_compare but inject train-window funders into analyze_launch."""
    from smartalpha.backtest_funders import ExitCompareResult, _entry_signaled
    from smartalpha.exit_rules import EXIT_MODES, exit_params, sim_exit
    from smartalpha.funder import dex_token_outcome
    from smartalpha.launch_intel import analyze_launch

    s = settings
    rpc = SolanaRpc(rpc_url(s))
    slip = s.backtest_slippage
    ep = exit_params(s)
    tp, sl = s.backtest_tp_pct, s.backtest_sl_pct

    modes = {m: {"net_tpsl_sol": 0.0, "wins": 0, "losses": 0} for m in EXIT_MODES}
    res = ExitCompareResult(
        mints_scanned=0,
        signals=0,
        skipped=0,
        liquidity_filtered=0,
        position_sol=position_sol,
        modes=modes,
        notes=["walk-forward test window; train funders only"],
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
            balanced=False,
            legacy=False,
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
                pnl_v, _ = sim_exit(mode, outcome, position_sol, slip, ep, tp_pct=tp, sl_pct=sl)
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


def write_walk_forward_report(result: WalkForwardResult, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "walk_forward.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "split_mode": result.split_mode,
        "train_ratio": result.train_ratio,
        "train_days": result.train_days,
        "test_days": result.test_days,
        "anchor_ts": result.anchor_ts,
        "train_window_unix": result.train_window,
        "test_window_unix": result.test_window,
        "train_mints": result.train_mints,
        "test_mints": result.test_mints,
        "skipped_no_pair_created_at": result.skipped_no_ts,
        "train_funders": result.train_funders,
        "test_compare": result.test_compare,
        "notes": result.notes,
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p
