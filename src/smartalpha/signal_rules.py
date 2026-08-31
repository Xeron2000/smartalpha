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
    max_buyer_share: float = 0.15,
) -> bool:
    """Pillar 2: Check unique buyers, buy/sell flow, and copytrap risk."""
    if not intel.buyers:
        return False
    unique_wallets = set(b.wallet for b in intel.buyers)
    if len(unique_wallets) < min_unique_buyers:
        return False
    if intel.copytrap_risk == "high":
        return False
    if max_buyer_share > 0 and intel.top_buyer_share >= max_buyer_share:
        return False

    # Older callers only supplied deduplicated buyers, so use that as a safe fallback.
    buys = intel.buy_count or len(intel.buyers)
    sells = intel.sell_count
    if buys / max(1, sells) < min_buy_sell_ratio:
        return False
    return True


def velocity_ok(
    volume_usd: float | None,
    liquidity_usd: float | None,
    min_velocity: float = 0.5,
    *,
    allow_unknown: bool = False,
) -> bool:
    """Pillar 3: Turnover velocity = observed volume / reserve."""
    if min_velocity <= 0:
        return True
    if volume_usd is None or liquidity_usd is None or liquidity_usd <= 0:
        return allow_unknown
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
    require_pair: bool = True,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 3000.0,
    volume_usd: float | None = None,
    min_velocity: float = 0.5,
    min_buy_sell_ratio: float = 1.5,
    max_buyer_share: float = 0.15,
    allow_unknown_liq: bool = False,
    allow_unknown_velocity: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> SignalLevel:
    """Classify a launch using observable microstructure only.

    STRONG is a hard entry gate. Funder labels can annotate a launch but cannot
    replace unique-buyer, buy/sell, liquidity, or volume gates.
    """
    if intel.copytrap_risk == "high":
        return SignalLevel.SKIP
    if any("strict disabled" in note for note in intel.notes):
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
    vel_ok = velocity_ok(
        volume_usd,
        liquidity_usd,
        min_velocity=min_velocity,
        allow_unknown=allow_unknown_velocity,
    )

    unique_wallets = {b.wallet for b in intel.buyers}
    n_unique = len(unique_wallets)
    entropy_ok = entropy_and_buyers_ok(
        intel,
        min_unique_buyers=min_unique_buyers,
        min_buy_sell_ratio=min_buy_sell_ratio,
        max_buyer_share=max_buyer_share,
    )

    # STRONG: all four first-principles gates pass.
    if liq_ok and entropy_ok and vel_ok:
        return SignalLevel.STRONG

    # MEDIUM/WATCH are observation states, never strict entry permission.
    if liq_ok and n_unique >= 3:
        return SignalLevel.MEDIUM
    if n_unique >= 2:
        return SignalLevel.WATCH
    return SignalLevel.SKIP


def should_follow_launch(
    intel: LaunchIntel,
    *,
    min_unique_buyers: int = 8,
    require_pair: bool = True,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 3000.0,
    volume_usd: float | None = None,
    min_velocity: float = 0.5,
    min_buy_sell_ratio: float = 1.5,
    max_buyer_share: float = 0.15,
    allow_unknown_liq: bool = False,
    allow_unknown_velocity: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> bool:
    """Strict entry = STRONG only (all observable gates required)."""
    return (
        classify_signal(
            intel,
            min_unique_buyers=min_unique_buyers,
            require_pair=require_pair,
            liquidity_usd=liquidity_usd,
            min_liquidity_usd=min_liquidity_usd,
            volume_usd=volume_usd,
            min_velocity=min_velocity,
            min_buy_sell_ratio=min_buy_sell_ratio,
            max_buyer_share=max_buyer_share,
            allow_unknown_liq=allow_unknown_liq,
            allow_unknown_velocity=allow_unknown_velocity,
            pair_age_hours=pair_age_hours,
            ignore_stale_low_liq=ignore_stale_low_liq,
        )
        == SignalLevel.STRONG
    )
