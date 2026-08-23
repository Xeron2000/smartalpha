"""Feature Registry — every feature usable by the generator must be registered."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Capability = Literal["FULL", "PARTIAL", "PROSPECTIVE_ONLY", "NONE"]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    type: str  # integer, float, boolean, ratio
    capability: Capability
    earliest_available_sec: int
    extractor: str
    required_provider: str = "helius"
    cost: str = "low"
    description: str = ""
    completeness: str = "window_complete must be true"


REGISTRY: dict[str, FeatureSpec] = {
    "fresh_wallets": FeatureSpec(
        name="fresh_wallets",
        type="integer",
        capability="FULL",
        earliest_available_sec=30,
        extractor="wallet_age",
        description="Alias for fresh_wallet_count",
    ),
    "hot_organic_buyers": FeatureSpec(
        name="hot_organic_buyers",
        type="integer",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Alias for hot_organic_count",
    ),
    "hot_organic": FeatureSpec(
        name="hot_organic",
        type="integer",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Alias for hot_organic_count",
    ),
    "liquidity_usd": FeatureSpec(
        name="liquidity_usd",
        type="float",
        capability="FULL",
        earliest_available_sec=0,
        extractor="dex",
        description="Liquidity USD at t0",
    ),
    "bundler_wallets": FeatureSpec(
        name="bundler_wallets",
        type="integer",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Alias for bundler_ratio",
    ),
    "copytrap_risk": FeatureSpec(
        name="copytrap_risk",
        type="string",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Copytrap risk level",
    ),
    "gain_1h_pct": FeatureSpec(
        name="gain_1h_pct",
        type="float",
        capability="FULL",
        earliest_available_sec=3600,
        extractor="kline",
        description="1h gain via Kline",
    ),
    "fresh_wallet_count": FeatureSpec(
        name="fresh_wallet_count",
        type="integer",
        capability="FULL",
        earliest_available_sec=30,
        extractor="wallet_age",
        required_provider="helius",
        description="Count of early buyers with wallet age <2h at as_of",
    ),
    "hot_organic_count": FeatureSpec(
        name="hot_organic_count",
        type="integer",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Hot organic buyers within 90s",
    ),
    "repeated_funder": FeatureSpec(
        name="repeated_funder",
        type="boolean",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Whether a hot funder repeats from train set",
    ),
    "top10_holder_rate": FeatureSpec(
        name="top10_holder_rate",
        type="float",
        capability="PROSPECTIVE_ONLY",
        earliest_available_sec=30,
        extractor="gmgn",
        required_provider="gmgn",
        description="Top10 holder concentration, prospective only",
    ),
    "bundler_ratio": FeatureSpec(
        name="bundler_ratio",
        type="float",
        capability="FULL",
        earliest_available_sec=90,
        extractor="launch_intel",
        description="Bundler cluster ratio among early buyers",
    ),
    "buy_size_bucket": FeatureSpec(
        name="buy_size_bucket",
        type="string",
        capability="FULL",
        earliest_available_sec=30,
        extractor="launch_intel",
        description="Bucket of early buy sizes",
    ),
}


def get_feature(name: str) -> FeatureSpec | None:
    return REGISTRY.get(name)


def list_features() -> list[FeatureSpec]:
    return list(REGISTRY.values())


def validate_feature_names(names: list[str]) -> tuple[bool, str]:
    for n in names:
        if n not in REGISTRY:
            return False, f"unknown feature: {n}"
    return True, "ok"


def is_historical_available(name: str, as_of_sec: int) -> bool:
    spec = REGISTRY.get(name)
    if not spec:
        return False
    if spec.capability == "PROSPECTIVE_ONLY":
        return False
    return as_of_sec >= spec.earliest_available_sec
