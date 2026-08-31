from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


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
