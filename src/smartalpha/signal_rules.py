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
    min_hot_buyers: int = 2,
    require_pair: bool = True,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 0,
    allow_unknown_liq: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> SignalLevel:
    """Classify launch into 4 levels.

    Funder-injected mode (session / walk-forward):
      STRONG - ≥min_hot_buyers hot organic + not high copytrap + pair + liq OK
      MEDIUM - ≥1 hot organic + pair (liq soft) OR follow_cohort + hot hit
      WATCH  - hot funder hit but weak organic / liq
      SKIP   - copytrap high or no hot signal

    Behavior-only (no funder prelist):
      STRONG - ≥1 high-confidence buyer + liq OK
      MEDIUM - ≥1 high-confidence buyer
    """
    if intel.copytrap_risk == "high":
        return SignalLevel.SKIP

    organic = high_confidence_buyers(intel)
    pair_ok = not require_pair or not any("no dex pair" in n for n in intel.notes)
    liq_ok = liquidity_ok(
        liquidity_usd,
        min_liquidity_usd,
        allow_unknown=allow_unknown_liq,
        pair_age_hours=pair_age_hours,
        ignore_stale_low_liq=ignore_stale_low_liq,
    )

    # Funder-injected mode
    if intel.funder_injected:
        hot_organic = hot_organic_buyers(intel)
        n_hot = len(hot_organic)
        has_hot = bool(intel.hot_funder_hits) or n_hot > 0

        if not has_hot or not pair_ok:
            return SignalLevel.SKIP

        # STRONG: real cohort of hot-funded organic buyers + liq
        if n_hot >= min_hot_buyers and liq_ok:
            return SignalLevel.STRONG

        # MEDIUM: at least one hot organic, or follow_cohort with hot hit
        if n_hot >= 1:
            return SignalLevel.MEDIUM
        if intel.recommendation == "follow_cohort" and has_hot:
            return SignalLevel.MEDIUM
        if has_hot:
            return SignalLevel.WATCH
        return SignalLevel.SKIP

    # Behavior-only
    if len(organic) >= 1 and pair_ok and liq_ok:
        return SignalLevel.STRONG
    if len(organic) >= 1:
        return SignalLevel.MEDIUM
    return SignalLevel.SKIP


def should_follow_launch(
    intel: LaunchIntel,
    *,
    min_hot_buyers: int = 2,
    require_pair: bool = True,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 0,
    allow_unknown_liq: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> bool:
    """Strict entry = STRONG only."""
    return (
        classify_signal(
            intel,
            min_hot_buyers=min_hot_buyers,
            require_pair=require_pair,
            liquidity_usd=liquidity_usd,
            min_liquidity_usd=min_liquidity_usd,
            allow_unknown_liq=allow_unknown_liq,
            pair_age_hours=pair_age_hours,
            ignore_stale_low_liq=ignore_stale_low_liq,
        )
        == SignalLevel.STRONG
    )


def should_follow_launch_legacy(intel: LaunchIntel) -> bool:
    if intel.copytrap_risk == "high":
        return False
    # Loose: any hot funder hit (rec may be skip under new scoring)
    return bool(intel.hot_funder_hits)


def should_follow_launch_balanced(
    intel: LaunchIntel,
    *,
    min_hot_buyers: int = 2,
    liquidity_usd: float | None = None,
    min_liquidity_usd: float = 0,
    allow_unknown_liq: bool = False,
    pair_age_hours: float | None = None,
    ignore_stale_low_liq: bool = False,
) -> bool:
    """STRONG or MEDIUM."""
    level = classify_signal(
        intel,
        min_hot_buyers=min_hot_buyers,
        liquidity_usd=liquidity_usd,
        min_liquidity_usd=min_liquidity_usd,
        allow_unknown_liq=allow_unknown_liq,
        pair_age_hours=pair_age_hours,
        ignore_stale_low_liq=ignore_stale_low_liq,
    )
    return level in (SignalLevel.STRONG, SignalLevel.MEDIUM)
