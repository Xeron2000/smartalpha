from __future__ import annotations

import json
from pathlib import Path

from smartalpha.backtest import run_demo_backtest
from smartalpha.cluster import ClusterEngine
from smartalpha.config import Settings
from smartalpha.copytrap import check_copytrap
from smartalpha.db import Store
from smartalpha.dump_detect import analyze_dump
from smartalpha.launch_intel import (
    BuyerProfile,
    _detect_bundlers,
    _recommendation,
    _score_buyer,
)
from smartalpha.pump import is_pump_create_logs, parse_pump_create_tx
from smartalpha.types import Side, TradeEvent

DEMO_MINT = "DemoMint1111111111111111111111111111111111"


def demo_events() -> list[TradeEvent]:
    """Synthetic smart-money cluster buy then coordinated dump."""
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


def demo_copytrap_events() -> list[TradeEvent]:
    base = 1_700_000_100
    evs = [
        TradeEvent("Bait1111111111111111111111111111111111", DEMO_MINT, Side.BUY, 2.0, 1000, "b0", base),
    ]
    for i in range(8):
        evs.append(
            TradeEvent(
                f"Copy{i}111111111111111111111111111111111",
                DEMO_MINT,
                Side.BUY,
                0.1,
                10,
                f"c{i}",
                base + i,
            )
        )
    return evs


def run_self_check() -> bool:
    events = demo_events()
    cluster = ClusterEngine(window_sec=300, min_wallets=3, min_score=4.0)
    alerts = cluster.load_events(events)
    assert len(alerts) >= 1, "cluster detection failed"

    report = analyze_dump(events, DEMO_MINT)
    assert report.score >= 60, f"dump score too low: {report.score}"
    assert report.unique_sellers >= 2

    bt = run_demo_backtest(events)
    assert bt.trades >= 3
    assert bt.wins + bt.losses >= 1

    out = Path(__file__).resolve().parents[2] / "demo" / "events.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            [
                {
                    "wallet": e.wallet,
                    "mint": e.mint,
                    "side": e.side.value,
                    "sol_delta": e.sol_delta,
                    "token_delta": e.token_delta,
                    "signature": e.signature,
                    "ts": e.ts,
                    "tier": e.tier,
                    "weight": e.weight,
                }
                for e in events
            ],
            indent=2,
        )
    )
    store = Store(Settings().db_path)
    for e in events:
        store.save_event(e)

    _self_check_launch_intel()
    _self_check_session_funders()
    _self_check_pump_parse()
    _self_check_mint_sources()
    _self_check_gmgn_cookie_parse()
    _self_check_walk_forward_split()
    _self_check_signal_rules()
    trap = check_copytrap("Bait1111111111111111111111111111111111", demo_copytrap_events())
    assert trap.risk in ("medium", "high")

    print("self-check OK")
    print(f"  cluster alerts: {len(alerts)}")
    print(f"  dump score: {report.score}")
    print(f"  backtest net_sol: {bt.net_sol:.4f} trades={bt.trades}")
    print(f"  copytrap: {trap.risk}")
    return True


def _self_check_launch_intel() -> None:
    buyers = [
        BuyerProfile(
            "Fresh1", 0.8, 1, "s1", slot=100, wallet_age_hours=0.5,
            funder="FunderHot111111111111111111111111111111111", funder_known=True,
        ),
        BuyerProfile(
            "Fresh2", 0.6, 2, "s2", slot=100, wallet_age_hours=0.6,
            funder="FunderHot111111111111111111111111111111111", funder_known=True,
        ),
        BuyerProfile(
            "Fresh3", 0.7, 3, "s3", slot=101, wallet_age_hours=0.4,
            funder="Other11111111111111111111111111111111111",
        ),
    ]
    fmap = {b.wallet: b.funder for b in buyers}
    bundlers = _detect_bundlers(buyers, fmap)
    for b in buyers:
        _score_buyer(b, bundlers)
    rec = _recommendation(
        buyers,
        bundlers,
        ["FunderHot111111111111111111111111111111111"],
    )
    assert rec in ("follow_cohort", "watch", "skip")
    print(f"  launch-intel rec: {rec}")


def _self_check_session_funders() -> None:
    from smartalpha.funder_score import (
        FunderGrade,
        enrich_funder_scores,
        grade_rank,
        score_funder_mints,
    )
    from smartalpha.session_funders import (
        _grade_rank,
        _parse_grade,
        build_hot_funders_from_recommended,
    )

    assert _parse_grade("medium") == FunderGrade.MEDIUM
    assert _grade_rank("strong") > _grade_rank("watch")
    assert _grade_rank("skip") < _grade_rank(FunderGrade.MEDIUM.value)
    assert grade_rank("medium") == 2

    # Discovery gains must produce medium/strong (not live-h24 death spiral)
    q = score_funder_mints(
        ["mintA", "mintB", "mintC"],
        mint_gains={"mintA": 500.0, "mintB": 1200.0, "mintC": 80.0},
        fetch_live=False,
        sleep=0.0,
    )
    assert q["score_source"] == "discovery"
    assert q["win_rate"] >= 0.5  # 2/3 wins at +100% threshold
    assert q["grade"] in ("medium", "strong"), q
    enriched = enrich_funder_scores(
        [{"address": "F1", "weight": 1.0, "mints": ["mintA", "mintB"]}],
        mint_gains={"mintA": 800.0, "mintB": 300.0},
        fetch_live=False,
        sleep=0.0,
    )
    assert enriched[0]["quality"]["grade"] in ("medium", "strong")

    hot, notes = build_hot_funders_from_recommended(
        [
            {
                "address": "FunderHot111111111111111111111111111111111",
                "label": "cross-2-mints",
                "weight": 1.5,
                "quality": {"grade": "strong"},
            },
            {
                "address": "FunderBad11111111111111111111111111111111",
                "label": "cross-2-mints",
                "weight": 1.0,
                "quality": {"grade": "watch"},
            },
        ],
        enrich=False,
        min_grade="medium",
    )
    assert len(hot) == 1
    assert "FunderHot" in next(iter(hot))
    assert any("session funders" in n for n in notes)

    # disk cache loader (used by watch-launches startup)
    from smartalpha.config import ROOT
    from smartalpha.session_funders import load_session_hot_funders_from_disk

    cache = ROOT / "data" / "auto_discover.json"
    if cache.exists():
        disk_hot, disk_notes, _ = load_session_hot_funders_from_disk(min_grade="medium")
        assert isinstance(disk_hot, dict)
        assert any("loaded disk" in n for n in disk_notes)
    print("  session-funders: ok")


def _self_check_mint_sources() -> None:
    from smartalpha.mint_sources import _is_pump_mint, _is_pump_pair

    assert _is_pump_mint("B9TycJVwp5c97bHvBUxNakjSZguvp8Jj2VPD6LCTpump")
    assert not _is_pump_mint("So11111111111111111111111111111111111111112")
    assert _is_pump_pair("xxxpump", "meteora")
    assert _is_pump_pair("xxx", "pumpfun")
    print("  mint-sources: ok")


def _self_check_gmgn_cookie_parse() -> None:
    from smartalpha.gmgn_cookie import parse_cookie, serialize_cookiejar

    jar = parse_cookie("sid=abc; _wt=xyz; __cf_bm=old; intercom=x")
    assert jar["sid"] == "abc"
    out = serialize_cookiejar(jar, prefer_keys=("sid", "_wt", "__cf_bm"))
    assert "sid=abc" in out and "intercom" not in out.split(";")[0]
    print("  gmgn-cookie: ok")


def _self_check_walk_forward_split() -> None:
    from smartalpha.walk_forward import split_mints_by_window, split_mints_chronological

    anchor = 1_700_000_000
    times = {
        "train_m": anchor - 10 * 86400,
        "test_m": anchor - 2 * 86400,
        "old": anchor - 30 * 86400,
    }
    train, test, _, _ = split_mints_by_window(
        times, train_days=7, test_days=7, anchor_ts=anchor
    )
    assert "train_m" in train and "test_m" in test and "old" not in train + test
    ch_train, ch_test, _, _ = split_mints_chronological(times, train_ratio=0.67)
    assert len(ch_train) == 2 and len(ch_test) == 1
    from smartalpha.funder_score import _adjusted_weight

    assert _adjusted_weight(1.0, {"win_rate": 0.2, "rug_rate": 0.6}) < 1.0
    print("  walk-forward split: ok")


def _self_check_signal_rules() -> None:
    from smartalpha.exit_rules import (
        sim_dynamic_exit,
        sim_fixed_tp_sl,
        sim_hybrid_exit,
        sim_ladder_exit,
        sim_scale_half,
    )
    from smartalpha.launch_intel import BuyerProfile, LaunchIntel
    from smartalpha.signal_rules import (
        should_follow_launch,
        should_follow_launch_balanced,
        should_follow_launch_legacy,
    )

    hot_f = "FunderHot111111111111111111111111111111111"
    intel = LaunchIntel(
        mint="m",
        buyers=[
            BuyerProfile("w1", 1.0, 1, "s1", funder=hot_f, funder_known=True, follow_score=40),
            BuyerProfile("w2", 1.0, 2, "s2", funder=hot_f, funder_known=True, follow_score=40),
        ],
        bundler_wallets=[],
        hot_funder_hits=[hot_f],
        copytrap_risk="low",
        recommendation="follow_cohort",
        funder_injected=True,
    )
    assert should_follow_launch(intel, min_hot_buyers=2, min_liquidity_usd=0)
    # 1 hot organic → MEDIUM, not STRONG when min_hot_buyers=2
    assert not should_follow_launch(
        LaunchIntel(
            "m",
            [BuyerProfile("w1", 1.0, 1, "s1", funder=hot_f, funder_known=True, follow_score=40)],
            [],
            [hot_f],
            "low",
            "follow_cohort",
            funder_injected=True,
        ),
        min_hot_buyers=2,
        min_liquidity_usd=0,
    )
    assert should_follow_launch_balanced(
        LaunchIntel(
            "m",
            [BuyerProfile("w1", 1.0, 1, "s1", funder=hot_f, funder_known=True, follow_score=40)],
            [],
            [hot_f],
            "low",
            "follow_cohort",
            funder_injected=True,
        ),
        min_hot_buyers=2,
        min_liquidity_usd=0,
    )
    # Stale low liq ignored in backtest mode
    assert should_follow_launch(
        intel,
        min_hot_buyers=2,
        liquidity_usd=100.0,
        min_liquidity_usd=5000,
        pair_age_hours=100.0,
        ignore_stale_low_liq=True,
    )
    assert not should_follow_launch_legacy(
        LaunchIntel("m", [], [], [], "high", "skip")
    )
    assert should_follow_launch_legacy(
        LaunchIntel("m", [], [], [hot_f], "low", "skip", funder_injected=True)
    )
    pnl, reason = sim_dynamic_exit({"h1": 60, "h6": 90, "h24": 55}, 0.5, 0.15)
    assert pnl is not None and reason and reason.startswith("trail@")
    pnl2, _ = sim_fixed_tp_sl({"h1": 5, "h6": 120, "h24": 80}, 100, 30, 0.5, 0.15)
    assert pnl2 is not None
    # 2x at h6, runner rugs to -90% h24: beats full hold but slippage may still net red
    pnl3, reason3 = sim_scale_half({"h1": 50, "h6": 120, "h24": -90}, 0.5, 0.15)
    pnl_full, _ = sim_scale_half({"h1": 50, "h6": -50, "h24": -90}, 0.5, 0.15)
    assert pnl3 is not None and reason3 == "half@h6+h24" and pnl3 > (pnl_full or -999)
    # rug h1: hybrid early cut beats scale full hold
    pnl4, reason4 = sim_hybrid_exit({"h1": -5, "h6": -80, "h24": -95}, 0.5, 0.15)
    assert pnl4 is not None and reason4 == "early@h1"
    # moon: hybrid scale + runner at h24
    pnl5, reason5 = sim_hybrid_exit({"h1": 80, "h6": 200, "h24": 800}, 0.5, 0.15)
    pnl_moon, _ = sim_scale_half({"h1": 80, "h6": 200, "h24": 800}, 0.5, 0.15)
    assert pnl5 is not None and reason5 == "half@h6+h24" and pnl5 == pnl_moon
    pnl6, reason6 = sim_ladder_exit({"h1": 20, "h6": 250, "h24": 400}, 0.5, 0.15)
    assert pnl6 is not None and "tp1@" in reason6 and "tp2@" in reason6
    print("  signal-rules: ok")


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
