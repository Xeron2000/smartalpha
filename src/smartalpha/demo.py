from __future__ import annotations

import time
from pathlib import Path

from smartalpha.db import Store
from smartalpha.exit_rules import ExitPolicy, simulate_exit
from smartalpha.launch_intel import BuyerProfile, LaunchIntel
from smartalpha.pump import is_pump_create_logs, parse_pump_create_tx
from smartalpha.signal_rules import SignalLevel, classify_signal


def run_self_check() -> bool:
    """Offline sanity check for all first-principles launch engine components."""
    print("Running SmartAlpha self-check...")
    _self_check_signal_rules()
    _self_check_db_paper()
    _self_check_pump_parse()
    print("All self-checks passed cleanly.")
    return True


def _self_check_signal_rules() -> None:
    # One deployment smoke case for the strict gate.
    buyers_8 = [BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(8)]
    intel_strong = LaunchIntel(
        mint="m",
        buyers=buyers_8,
        bundler_wallets=[],
        copytrap_risk="low",
        recommendation="follow_cohort",
        buy_count=8,
        sell_count=4,
    )
    assert classify_signal(
        intel_strong,
        min_unique_buyers=8,
        liquidity_usd=5000.0,
        min_liquidity_usd=5000.0,
        volume_usd=4000.0,
    ) == SignalLevel.STRONG
    # Shared exit policy smoke case.
    pnl, reason = simulate_exit(
        {"h1": -25, "h6": None, "h24": None},
        0.5,
        0.15,
        policy=ExitPolicy(max_hold_sec=0),
    )
    assert pnl is not None and reason == "stop_loss@h1"
    print("  exit-policy: ok")


def _self_check_db_paper() -> None:
    import tempfile

    from smartalpha.paper_log import paper_health

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
