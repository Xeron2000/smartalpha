#!/usr/bin/env python3
"""ponytail: sweep session-injected backtest knobs; writes data/param_sweep.json."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartalpha.auto_discover import run_auto_discover, write_auto_discover_report
from smartalpha.backtest_funders import load_mints_with_pairs, run_funder_backtest
from smartalpha.config import Settings


def make_settings(**overrides: object) -> Settings:
    s = Settings()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


CONFIGS: list[tuple[str, dict[str, object]]] = [
    ("baseline", {}),
    ("liq_5k", {"signal_min_liquidity_usd": 5000.0}),
    ("hot_buyers_1", {"signal_min_hot_buyers": 1}),
    ("grade_watch", {"session_min_grade": "watch"}),
    ("liq5k_hot1", {"signal_min_liquidity_usd": 5000.0, "signal_min_hot_buyers": 1}),
    (
        "combined_relaxed",
        {
            "signal_min_liquidity_usd": 5000.0,
            "signal_min_hot_buyers": 1,
            "session_min_grade": "watch",
        },
    ),
]


def summarize(name: str, result, params: dict[str, object]) -> dict:
    closed = result.wins_tpsl + result.losses_tpsl
    return {
        "name": name,
        "params": params,
        "signals": result.signals,
        "net_tpsl_sol": round(result.net_tpsl, 4),
        "net_h24_sol": round(result.net_h24, 4),
        "win_rate": round(result.wins_tpsl / closed, 3) if closed else None,
        "wins": result.wins_tpsl,
        "losses": result.losses_tpsl,
        "liquidity_filtered": result.liquidity_filtered,
        "session_notes": [n for n in result.notes if "session" in n],
        "signaled_trades": [
            {
                "mint": t.mint[:16] + "...",
                "hot_funders": [f[:8] + "..." for f in t.hot_funders],
                "hot_organic_buyers": t.hot_organic_buyers,
                "signal_level": t.signal_level,
                "liquidity_usd": t.liquidity_usd,
                "gain_h24_pct": t.gain_h24_pct,
                "pnl_tpsl_sol": round(t.pnl_tpsl_sol, 4) if t.pnl_tpsl_sol is not None else None,
            }
            for t in result.trades
            if t.signaled
        ],
    }


def save_payload(out: Path, *, mints_baseline: int, mints_pool25: int, runs: list[dict]) -> None:
    payload = {
        "mints_baseline": mints_baseline,
        "mints_pool25": mints_pool25,
        "runs": runs,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def run_sweep(
    source: Path,
    mints,
    configs: list[tuple[str, dict]],
    *,
    out: Path,
    all_runs: list[dict],
    mints_baseline: int,
    mints_pool25: int,
) -> list[dict]:
    rows: list[dict] = []
    for name, params in configs:
        print(f"\n=== {name} @ {source.name} ===", flush=True)
        t0 = time.time()
        result = run_funder_backtest(
            mints,
            settings=make_settings(**params),
            mints_source=source,
            exit_mode="scale",
        )
        row = summarize(name, result, params)
        row["elapsed_sec"] = round(time.time() - t0)
        row["source"] = str(source)
        rows.append(row)
        all_runs.append(row)
        save_payload(out, mints_baseline=mints_baseline, mints_pool25=mints_pool25, runs=all_runs)
        print(json.dumps(row, indent=2, ensure_ascii=False), flush=True)
    return rows


def main() -> None:
    base_source = ROOT / "data" / "auto_discover.json"
    mints = load_mints_with_pairs(base_source)
    out = ROOT / "data" / "param_sweep.json"
    all_runs: list[dict] = []
    done: set[str] = set()
    if out.exists():
        prev = json.loads(out.read_text())
        all_runs = list(prev.get("runs") or [])
        done = {r["name"] for r in all_runs}

    pending = [(n, p) for n, p in CONFIGS if n not in done]
    expanded_path = ROOT / "data" / "auto_discover_25.json"
    expanded_mints = load_mints_with_pairs(expanded_path) if expanded_path.exists() else []
    if pending:
        run_sweep(
            base_source,
            mints,
            pending,
            out=out,
            all_runs=all_runs,
            mints_baseline=len(mints),
            mints_pool25=len(expanded_mints),
        )

    if not expanded_path.exists():
        print("\n=== auto-discover limit=25 ===", flush=True)
        s = Settings()
        s.session_mint_limit = 25
        discover = run_auto_discover(s, mint_limit=25, min_gain_pct=s.session_min_gain_pct)
        expanded_path = write_auto_discover_report(discover, path=expanded_path)
        print(
            f"expanded mints={len(load_mints_with_pairs(expanded_path))} "
            f"funders={len(getattr(discover.discover, 'recommended', []))}",
            flush=True,
        )

    expanded_mints = load_mints_with_pairs(expanded_path)
    pool_configs = [
        ("pool25_baseline", {}),
        ("pool25_combined_relaxed", CONFIGS[-1][1]),
    ]
    pending_pool = [(n, p) for n, p in pool_configs if n not in {r["name"] for r in all_runs}]
    if pending_pool:
        run_sweep(
            expanded_path,
            expanded_mints,
            pending_pool,
            out=out,
            all_runs=all_runs,
            mints_baseline=len(mints),
            mints_pool25=len(expanded_mints),
        )

    save_payload(
        out,
        mints_baseline=len(mints),
        mints_pool25=len(expanded_mints),
        runs=all_runs,
    )
    print(f"\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
