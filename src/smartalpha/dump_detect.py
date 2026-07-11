from __future__ import annotations

from collections import defaultdict

from smartalpha.config import Settings
from smartalpha.types import DumpReport, DumpSignal, Side, TradeEvent


def analyze_dump(
    events: list[TradeEvent],
    mint: str,
    *,
    settings: Settings | None = None,
) -> DumpReport:
    s = settings or Settings()
    scoped = [e for e in events if e.mint == mint]

    buy_sol = sum(abs(e.sol_delta) for e in scoped if e.side == Side.BUY)
    sell_sol = sum(abs(e.sol_delta) for e in scoped if e.side == Side.SELL)
    sellers = {e.wallet for e in scoped if e.side == Side.SELL}
    buyers = {e.wallet for e in scoped if e.side == Side.BUY}

    signals: list[DumpSignal] = []
    score = 0.0

    total = buy_sol + sell_sol
    sell_ratio = sell_sol / total if total > 0 else 0.0
    if total > 0 and sell_ratio >= s.dump_sell_ratio:
        signals.append(
            DumpSignal(
                kind="sell_pressure",
                detail=f"sell_ratio={sell_ratio:.2f} (buy={buy_sol:.4f} sell={sell_sol:.4f} SOL)",
                severity="high",
            )
        )
        score += 40

    if len(sellers) >= s.dump_min_sellers:
        signals.append(
            DumpSignal(
                kind="coordinated_sellers",
                detail=f"{len(sellers)} smart wallets selling",
                severity="high",
            )
        )
        score += 35

    # sniper exit: multiple sells within short span after prior buys
    sells_by_wallet = defaultdict(list)
    for e in scoped:
        if e.side == Side.SELL:
            sells_by_wallet[e.wallet].append(e)
    fast_exits = sum(1 for ws in sells_by_wallet.values() if ws)
    if fast_exits >= 2 and sell_sol > buy_sol * 0.5:
        signals.append(
            DumpSignal(
                kind="smart_money_exit",
                detail="smart wallets net distributing",
                severity="medium",
            )
        )
        score += 25

    if not signals:
        signals.append(
            DumpSignal(kind="ok", detail="no dump pattern in window", severity="low")
        )

    return DumpReport(
        mint=mint,
        buy_sol=buy_sol,
        sell_sol=sell_sol,
        unique_sellers=len(sellers),
        unique_buyers=len(buyers),
        signals=signals,
        score=min(score, 100.0),
    )
