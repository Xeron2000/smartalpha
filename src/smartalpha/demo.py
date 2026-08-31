from __future__ import annotations

import time
from pathlib import Path

from smartalpha.copytrap import check_copytrap
from smartalpha.db import Store
from smartalpha.dump_detect import analyze_dump
from smartalpha.exit_rules import (
    sim_dynamic_exit,
    sim_fixed_tp_sl,
    sim_scale_half,
)
from smartalpha.launch_intel import (
    BuyerProfile,
    LaunchIntel,
)
from smartalpha.pump import is_pump_create_logs, parse_pump_create_tx
from smartalpha.signal_rules import (
    calculate_friction_net_gain,
    should_follow_launch,
)
from smartalpha.types import Side, TradeEvent

DEMO_MINT = "DemoMint1111111111111111111111111111111111"


def demo_events() -> list[TradeEvent]:
    """Synthetic buyer inflow then coordinated dump."""
    base = 1_700_000_000
    wallets = [
        ("WalletA1111111111111111111111111111111111", 1.2),
        ("WalletB1111111111111111111111111111111111", 1.0),
        ("WalletC1111111111111111111111111111111111", 1.1),
        ("WalletD1111111111111111111111111111111111", 0.9),
    ]
    events: list[TradeEvent] = []
    for i, (w, wt) in enumerate(wallets):
        events.append(
            TradeEvent(
                wallet=w,
                mint=DEMO_MINT,
                side=Side.BUY,
                sol_delta=0.5 + i * 0.1,
                token_delta=1000.0,
                signature=f"buy_sig_{i}",
                ts=base + i * 10,
                tier="accumulator",
                weight=wt,
            )
        )
    for i, (w, wt) in enumerate(wallets[:3]):
        events.append(
            TradeEvent(
                wallet=w,
                mint=DEMO_MINT,
                side=Side.SELL,
                sol_delta=-1.3,
                token_delta=-800.0,
                signature=f"sell_sig_{i}",
                ts=base + 600 + i * 5,
                tier="accumulator",
                weight=wt,
            )
        )
    return events


def run_self_check() -> bool:
    """Offline sanity check for all first-principles launch engine components."""
    print("Running SmartAlpha self-check...")
    _self_check_signal_rules()
    _self_check_copytrap()
    _self_check_dump()
    _self_check_db_paper()
    _self_check_pump_parse()
    print("All self-checks passed cleanly.")
    return True


def _self_check_signal_rules() -> None:
    # 1. Friction model
    net = calculate_friction_net_gain(1.0, 5000.0, trade_size_usd=100.0)
    assert 0.70 < net < 1.0
    assert calculate_friction_net_gain(-1.0, 5000.0) == -1.0

    # 2. Four Pillars Gate
    buyers_8 = [BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(8)]
    intel_strong = LaunchIntel(
        mint="m",
        buyers=buyers_8,
        bundler_wallets=[],
        hot_funder_hits=[],
        copytrap_risk="low",
        recommendation="follow_cohort",
    )
    assert should_follow_launch(intel_strong, min_unique_buyers=8, liquidity_usd=5000.0, min_liquidity_usd=3000.0)
    assert not should_follow_launch(intel_strong, min_unique_buyers=8, liquidity_usd=1000.0, min_liquidity_usd=3000.0)

    # 3. Exit rules simulation
    pnl, reason = sim_dynamic_exit({"h1": 60, "h6": 90, "h24": 55}, 0.5, 0.15)
    assert pnl is not None and reason and reason.startswith("trail@")
    pnl2, _ = sim_fixed_tp_sl({"h1": 5, "h6": 120, "h24": 80}, 100, 30, 0.5, 0.15)
    assert pnl2 is not None
    pnl3, reason3 = sim_scale_half({"h1": 50, "h6": 120, "h24": -90}, 0.5, 0.15)
    assert pnl3 is not None and reason3 == "half@h6+h24"
    print("  signal-rules: ok")


def _self_check_copytrap() -> None:
    events = demo_events()
    res = check_copytrap("WalletA1111111111111111111111111111111111", events)
    assert res.risk in ("high", "medium", "low")
    print("  copytrap: ok")


def _self_check_dump() -> None:
    report = analyze_dump(demo_events(), DEMO_MINT)
    assert report.unique_sellers >= 3
    print("  dump-detect: ok")


def _self_check_db_paper() -> None:
    import tempfile

    from smartalpha.paper_log import export_paper_csv, paper_health

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        store = Store(db)
        now = int(time.time())
        store.upsert_paper_signal(
            mint="mint_demo",
            signal_ts=now,
            creator="creator_demo",
            signature="sig_demo",
            recommendation="follow",
            copytrap_risk="low",
            hot_organic_buyers=8,
            hot_funders=[],
            liquidity_usd=5000.0,
            strict_signal=True,
            price_usd=0.001,
            snapshots={
                "0": {"price_usd": 0.001, "source": "dexscreener", "observed_at": now},
                "90": {"price_usd": 0.002, "liquidity_usd": 6000.0, "source": "dexscreener", "observed_at": now + 90},
            },
            notes="demo",
        )
        h = paper_health(store=store)
        assert h["paper_rows"] == 1 and h["strict_rows"] == 1
        csv_p = Path(td) / "p.csv"
        n = export_paper_csv(csv_p, store=store)
        assert n == 1
    print("  db-paper: ok")


def _self_check_pump_parse() -> None:
    fake_tx = {
        "meta": {
            "logMessages": ["Program log: Instruction: Create"],
            "postTokenBalances": [{"mint": "MintTest111111111111111111111111111111111", "uiTokenAmount": {}}],
        },
        "transaction": {"message": {"accountKeys": [{"pubkey": "Creator111111111111111111111111111111111"}]}},
    }
    assert is_pump_create_logs(fake_tx["meta"]["logMessages"])
    parsed = parse_pump_create_tx(fake_tx)
    assert parsed and parsed[0].startswith("MintTest")
    print("  pump-parse: ok")
