from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class WalletConfig:
    address: str
    tier: str = "accumulator"
    weight: float = 1.0
    label: str = ""


@dataclass(frozen=True)
class TradeEvent:
    wallet: str
    mint: str
    side: Side
    sol_delta: float
    token_delta: float
    signature: str
    ts: int
    tier: str = "accumulator"
    weight: float = 1.0


@dataclass
class ClusterAlert:
    mint: str
    wallets: list[str]
    score: float
    side: Side
    ts: int
    events: list[TradeEvent] = field(default_factory=list)


@dataclass
class DumpSignal:
    kind: str
    detail: str
    severity: str


@dataclass
class DumpReport:
    mint: str
    buy_sol: float
    sell_sol: float
    unique_sellers: int
    unique_buyers: int
    signals: list[DumpSignal]
    score: float
