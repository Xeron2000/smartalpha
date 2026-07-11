from __future__ import annotations

from dataclasses import dataclass

from smartalpha.types import TradeEvent


@dataclass
class CopyTrapReport:
    risk: str
    reasons: list[str]
    safe_to_mirror: bool


def check_copytrap(
    leader_wallet: str,
    events: list[TradeEvent],
    *,
    window_sec: int = 30,
) -> CopyTrapReport:
    """
    Detect bait patterns:
    - leader buys once, many followers in tight window (you'd be exit liq)
    - fresh wallet + large buy + no cohort
    """
    reasons: list[str] = []
    leader_buys = [e for e in events if e.wallet == leader_wallet and e.side.value == "buy"]
    if not leader_buys:
        return CopyTrapReport("low", ["no leader buy in window"], True)

    lb = leader_buys[0]
    followers = [
        e
        for e in events
        if e.wallet != leader_wallet
        and e.mint == lb.mint
        and e.side.value == "buy"
        and abs(e.ts - lb.ts) <= window_sec
    ]
    if len(followers) >= 5:
        reasons.append(f"{len(followers)} wallets bought within {window_sec}s after leader")
    if len(followers) >= 10:
        return CopyTrapReport("high", reasons, False)

    if len(followers) >= 5:
        return CopyTrapReport("medium", reasons, False)

    return CopyTrapReport("low", reasons or ["no copy swarm"], True)
