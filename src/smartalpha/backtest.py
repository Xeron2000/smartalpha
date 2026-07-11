from __future__ import annotations

import time
from dataclasses import dataclass, field

from smartalpha.config import Settings, load_wallets
from smartalpha.rpc import SolanaRpc, parse_wallet_swaps
from smartalpha.types import Side, TradeEvent, WalletConfig


@dataclass
class PaperPosition:
    mint: str
    leader: str
    entry_ts: int
    cost_sol: float
    token_qty: float


@dataclass
class BacktestResult:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    net_sol: float = 0.0
    gross_in: float = 0.0
    gross_out: float = 0.0
    by_wallet: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def run_backtest(
    *,
    days: int = 30,
    wallets: list[WalletConfig] | None = None,
    settings: Settings | None = None,
    rpc: SolanaRpc | None = None,
) -> BacktestResult:
    """Paper-copy leader swaps with slippage + delay penalty on SOL flows."""
    s = settings or Settings()
    wlist = wallets or load_wallets()
    if not wlist:
        return BacktestResult(notes=["no wallets — use demo/self-check or wallets.json"])

    rpc = rpc or SolanaRpc(s.rpc_url)
    since = int(time.time()) - days * 86400
    slip = s.backtest_slippage

    # collect leader events chronologically
    timeline: list[tuple[TradeEvent, WalletConfig]] = []
    for w in wlist:
        sigs = _fetch_since(rpc, w.address, since, max_sigs=200)
        for sig in reversed(sigs):
            tx = rpc.get_transaction(sig)
            for ev in parse_wallet_swaps(tx, w.address):
                if ev.ts >= since:
                    timeline.append((ev, w))
    timeline.sort(key=lambda x: x[0].ts)

    positions: dict[tuple[str, str], PaperPosition] = {}
    res = BacktestResult()

    for ev, w in timeline:
        key = (w.address, ev.mint)
        sol_amt = max(abs(ev.sol_delta), 0.001)

        if ev.side == Side.BUY:
            # follower pays worse price
            cost = sol_amt * (1 + slip)
            qty = abs(ev.token_delta) * (1 - slip * 0.5) if ev.token_delta else 1.0
            positions[key] = PaperPosition(
                mint=ev.mint,
                leader=w.address,
                entry_ts=ev.ts + s.backtest_delay,
                cost_sol=cost,
                token_qty=qty,
            )
            res.gross_in += cost
            res.trades += 1

        elif ev.side == Side.SELL and key in positions:
            pos = positions.pop(key)
            proceeds = sol_amt * (1 - slip)
            pnl = proceeds - pos.cost_sol
            res.net_sol += pnl
            res.gross_out += proceeds
            res.by_wallet[w.address] = res.by_wallet.get(w.address, 0.0) + pnl
            if pnl >= 0:
                res.wins += 1
            else:
                res.losses += 1

    if not timeline:
        res.notes.append("no swap events found — RPC rate limit or inactive wallets")
    return res


def _fetch_since(rpc: SolanaRpc, address: str, since_ts: int, max_sigs: int = 200) -> list[str]:
    out: list[str] = []
    before: str | None = None
    while len(out) < max_sigs:
        batch = rpc.get_signatures(address, before=before, limit=25)
        if not batch:
            break
        stop = False
        for item in batch:
            bt = item.get("blockTime")
            if bt and bt < since_ts:
                stop = True
                break
            if not item.get("err"):
                out.append(item["signature"])
        before = batch[-1]["signature"]
        if stop or len(batch) < 25:
            break
    return out[:max_sigs]


def run_demo_backtest(events: list[TradeEvent]) -> BacktestResult:
    """Offline backtest from injected events (self-check)."""
    s = Settings()
    slip = s.backtest_slippage
    positions: dict[tuple[str, str], PaperPosition] = {}
    res = BacktestResult()
    wallet_map = {e.wallet: WalletConfig(e.wallet, e.tier, e.weight) for e in events}

    for ev in sorted(events, key=lambda e: e.ts):
        w = wallet_map[ev.wallet]
        key = (w.address, ev.mint)
        sol_amt = max(abs(ev.sol_delta), 0.001)
        if ev.side == Side.BUY:
            cost = sol_amt * (1 + slip)
            positions[key] = PaperPosition(ev.mint, w.address, ev.ts, cost, abs(ev.token_delta))
            res.gross_in += cost
            res.trades += 1
        elif ev.side == Side.SELL and key in positions:
            pos = positions.pop(key)
            proceeds = sol_amt * (1 - slip)
            pnl = proceeds - pos.cost_sol
            res.net_sol += pnl
            res.gross_out += proceeds
            res.by_wallet[w.address] = res.by_wallet.get(w.address, 0.0) + pnl
            res.wins += int(pnl >= 0)
            res.losses += int(pnl < 0)
    return res
