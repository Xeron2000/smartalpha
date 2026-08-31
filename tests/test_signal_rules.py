from smartalpha.launch_intel import BuyerProfile, LaunchIntel
from smartalpha.signal_rules import SignalLevel, classify_signal, liquidity_ok, should_follow_launch


def _intel(**kwargs) -> LaunchIntel:
    base = dict(
        mint="m",
        buyers=[],
        bundler_wallets=[],
        copytrap_risk="low",
        recommendation="observe",
    )
    base.update(kwargs)
    return LaunchIntel(**base)


def test_liquidity_ok_unknown_and_stale():
    assert liquidity_ok(None, 5000, allow_unknown=True) is True
    assert liquidity_ok(None, 5000, allow_unknown=False) is False
    assert liquidity_ok(100.0, 5000, pair_age_hours=100, ignore_stale_low_liq=True) is True
    assert liquidity_ok(100.0, 5000, pair_age_hours=1, ignore_stale_low_liq=True) is False
    assert liquidity_ok(6000.0, 5000) is True


def test_strong_requires_all_first_principles_gates():
    buyers = [BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(8)]
    intel = _intel(buyers=buyers, buy_count=8, sell_count=4)
    assert (
        classify_signal(
            intel,
            liquidity_usd=8000,
            min_liquidity_usd=5000,
            volume_usd=4000,
        )
        == SignalLevel.STRONG
    )
    assert should_follow_launch(
        intel,
        liquidity_usd=8000,
        min_liquidity_usd=5000,
        volume_usd=4000,
    )


def test_strong_rejects_bad_buy_sell_ratio_or_unknown_volume():
    buyers = [BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(8)]
    bad_ratio = _intel(buyers=buyers, buy_count=8, sell_count=6)
    assert classify_signal(bad_ratio, liquidity_usd=8000, volume_usd=4000) != SignalLevel.STRONG
    no_volume = _intel(buyers=buyers, buy_count=8, sell_count=4)
    assert classify_signal(no_volume, liquidity_usd=8000) != SignalLevel.STRONG


def test_medium_is_observation_only():
    intel = _intel(
        buyers=[BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(3)]
    )
    assert classify_signal(intel, min_liquidity_usd=0) == SignalLevel.MEDIUM
    assert not should_follow_launch(intel, min_liquidity_usd=0)


def test_high_copytrap_skips():
    intel = _intel(
        buyers=[
            BuyerProfile("w1", 1.0, 1, "s1"),
            BuyerProfile("w2", 1.0, 2, "s2"),
        ],
        copytrap_risk="high",
    )
    assert classify_signal(intel, min_liquidity_usd=0) == SignalLevel.SKIP


def test_first_principles_friction_and_entropy():
    from smartalpha.signal_rules import (
        calculate_friction_net_gain,
        entropy_and_buyers_ok,
        velocity_ok,
    )

    # 1. Friction test
    # Gross 100% gain on 5k reserve with 100 size -> net return is positive but < 100% due to price impact/fees/gas
    net = calculate_friction_net_gain(1.0, 5000.0, trade_size_usd=100.0)
    assert 0.70 < net < 1.0

    # Dead reserve or -100% return
    assert calculate_friction_net_gain(-1.0, 5000.0) == -1.0
    assert calculate_friction_net_gain(1.0, 0.0) == -1.0

    # 2. Entropy & Unique Buyers
    intel_few = _intel(
        buyers=[
            BuyerProfile("w1", 1.0, 1, "s1"),
            BuyerProfile("w1", 1.0, 2, "s2"),  # Duplicate wallet
        ]
    )
    assert entropy_and_buyers_ok(intel_few, min_unique_buyers=8) is False

    buyers_8 = [BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(8)]
    intel_good = _intel(buyers=buyers_8, copytrap_risk="low")
    assert entropy_and_buyers_ok(intel_good, min_unique_buyers=8) is True

    # 3. Velocity check
    assert velocity_ok(3000.0, 5000.0, min_velocity=0.5) is True  # 0.6 >= 0.5
    assert velocity_ok(1000.0, 5000.0, min_velocity=0.5) is False  # 0.2 < 0.5

