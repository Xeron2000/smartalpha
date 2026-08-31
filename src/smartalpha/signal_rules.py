"""Entry signal levels and follow rules."""

from __future__ import annotations

from enum import StrEnum

from smartalpha.launch_intel import LaunchIntel


class SignalLevel(StrEnum):
    """Multi-level signal: higher = more confidence."""

    STRONG = "strong"
    MEDIUM = "medium"
    WATCH = "watch"
    SKIP = "skip"


def hot_organic_buyers(intel: LaunchIntel) -> list:
    """Buyers funded by known hot funder, not flagged as bundler."""
    bundlers = set(intel.bundler_wallets)
    return [
        b
        for b in intel.buyers
        if b.funder_known and b.wallet not in bundlers
    ]


def high_confidence_buyers(intel: LaunchIntel, threshold: int = 30) -> list:
    """Buyers with follow_score >= threshold, not bundlers."""
    bundlers = set(intel.bundler_wallets)
    return [
        b
        for b in intel.buyers
        if b.follow_score >= threshold and b.wallet not in bundlers
    ]


def calculate_friction_net_gain(
    gross_return: float,
    reserve_usd: float | None,
    trade_size_usd: float = 100.0,
    gas_usd: float = 0.50,
    fee_pct: float = 0.006,
) -> float:
    """First-principles net return after price impact, DEX fees, and fixed gas."""
    if gross_return <= -1.0 or reserve_usd is None or reserve_usd <= 0:
        return -1.0
    impact_in = min(0.5, trade_size_usd / (2.0 * max(100.0, reserve_usd)))
    dex_fee_half = fee_pct / 2.0
    exit_reserve = max(10.0, reserve_usd * (1.0 + gross_return))
    impact_out = min(0.5, trade_size_usd / (2.0 * exit_reserve))
    gas_pct = gas_usd / max(1.0, trade_size_usd)

    effective_entry = 1.0 + impact_in + dex_fee_half + gas_pct
    gross_exit = max(0.0, 1.0 + gross_return)
    effective_exit = gross_exit * (1.0 - impact_out - dex_fee_half) - gas_pct

    net_return = (effective_exit / effective_entry) - 1.0
    return max(-1.0, net_return)


def entropy_and_buyers_ok(
    intel: LaunchIntel,
    min_unique_buyers: int = 8,
    min_buy_sell_ratio: float = 1.5,
) -> bool:
    """Pillar 2: Check buyer dispersion & anti-sybil entropy."""
    if not intel.buyers:
        return False
    unique_wallets = set(b.wallet for b in intel.buyers)
    if len(unique_wallets) < min_unique_buyers:
        return False
    # If notes or buyer flags indicate extreme concentration
    if intel.copytrap_risk == "high":
        return False
    return True


def velocity_ok(
    volume_usd: float | None,
    liquidity_usd: float | None,
    min_velocity: float = 0.5,
) -> bool:
    """Pillar 3: Turnover velocity = Volume / Reserve >= min_velocity."""
    if min_velocity <= 0:
        return True
    if volume_usd is None or liquidity_usd is None or liquidity_usd <= 0:
        return True
    return (volume_usd / liquidity_usd) >= min_velocity


def liquidity_ok(
    liquidity_usd: float | None,
    min_liquidity_usd: float,
    *,
    allow_unknown: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
    stale_hours: float = 48.0,
) -> bool:
    """Pillar 1: Liquidity Guard gate (Reserve >= min_liquidity_usd)."""
    if min_liquidity_usd <= 0:
        return True
    if liquidity_usd is None:
        return allow_unknown
    if liquidity_usd >= min_liquidity_usd:
        return True
    if (
        ignore_stale_low_liq
        and pair_age_hours is not None
        and pair_age_hours >= stale_hours
    ):
        return True
    return False


def classify_signal(
    intel: LaunchIntel,
    *,
    min_unique_buyers: int = 8,
    min_hot_buyers: int = 2,
    require_pair: bool = True,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 3000.0,
    volume_usd: float | None = None,
    min_velocity: float = 0.5,
    allow_unknown_liq: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> SignalLevel:
    """Classify launch into 4 levels based purely on First-Principles Microstructure (STRATEGY_SPEC.md).

    Pillar 1: Liquidity Guard (Reserve >= min_liquidity_usd, default $3,000)
    Pillar 2: Orderflow Entropy (Unique Buyers >= min_unique_buyers, not single-sybil)
    Pillar 3: Turnover Velocity (Volume / Reserve >= min_velocity)
    Pillar 4: Copytrap & Anti-MEV Safety
    """
    if intel.copytrap_risk == "high":
        return SignalLevel.SKIP

    pair_ok = not require_pair or not any("no dex pair" in n for n in intel.notes)
    if not pair_ok:
        return SignalLevel.SKIP

    liq_ok = liquidity_ok(
        liquidity_usd,
        min_liquidity_usd,
        allow_unknown=allow_unknown_liq,
        pair_age_hours=pair_age_hours,
        ignore_stale_low_liq=ignore_stale_low_liq,
    )
    vel_ok = velocity_ok(volume_usd, liquidity_usd, min_velocity=min_velocity)

    # Buyer validation: True multi-buyer cohort (Unique buyers entropy or hot organic cohort)
    unique_wallets = set(b.wallet for b in intel.buyers)
    n_unique = len(unique_wallets)
    hot_organic = hot_organic_buyers(intel)
    organic = high_confidence_buyers(intel)
    if intel.funder_injected:
        has_cohort = (n_unique >= min_unique_buyers) or (len(hot_organic) >= min_hot_buyers)
    else:
        has_cohort = (n_unique >= min_unique_buyers) or (len(organic) >= 1)

    # STRONG: Full First-Principles Pass
    if liq_ok and has_cohort and vel_ok:
        return SignalLevel.STRONG

    # MEDIUM: Partial dispersion with acceptable liquidity
    if liq_ok and (n_unique >= 3 or len(hot_organic) >= 1 or len(organic) >= 1):
        return SignalLevel.MEDIUM

    # WATCH: Active flow but incomplete liquidity or small buyer set
    if n_unique >= 2 or len(hot_organic) >= 1:
        return SignalLevel.WATCH

    return SignalLevel.SKIP


def should_follow_launch(
    intel: LaunchIntel,
    *,
    min_unique_buyers: int = 8,
    min_hot_buyers: int = 2,
    require_pair: bool = True,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 3000.0,
    volume_usd: float | None = None,
    min_velocity: float = 0.5,
    allow_unknown_liq: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> bool:
    """Strict entry = STRONG only (Pillars 1, 2, 3, 4)."""
    return (
        classify_signal(
            intel,
            min_unique_buyers=min_unique_buyers,
            min_hot_buyers=min_hot_buyers,
            require_pair=require_pair,
            liquidity_usd=liquidity_usd,
            min_liquidity_usd=min_liquidity_usd,
            volume_usd=volume_usd,
            min_velocity=min_velocity,
            allow_unknown_liq=allow_unknown_liq,
            pair_age_hours=pair_age_hours,
            ignore_stale_low_liq=ignore_stale_low_liq,
        )
        == SignalLevel.STRONG
    )


def should_follow_launch_legacy(intel: LaunchIntel) -> bool:
    """Legacy loose check (deprecated)."""
    if intel.copytrap_risk == "high":
        return False
    return bool(intel.hot_funder_hits) or len(intel.buyers) >= 3


def should_follow_launch_balanced(
    intel: LaunchIntel,
    *,
    min_unique_buyers: int = 8,
    min_hot_buyers: int = 2,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 3000.0,
    volume_usd: float | None = None,
    min_velocity: float = 0.5,
    allow_unknown_liq: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> bool:
    """STRONG or MEDIUM entry."""
    level = classify_signal(
        intel,
        min_unique_buyers=min_unique_buyers,
        min_hot_buyers=min_hot_buyers,
        liquidity_usd=liquidity_usd,
        min_liquidity_usd=min_liquidity_usd,
        volume_usd=volume_usd,
        min_velocity=min_velocity,
        allow_unknown_liq=allow_unknown_liq,
        pair_age_hours=pair_age_hours,
        ignore_stale_low_liq=ignore_stale_low_liq,
    )
    return level in (SignalLevel.STRONG, SignalLevel.MEDIUM)
