from __future__ import annotations

from collections import defaultdict

from smartalpha.types import ClusterAlert, Side, TradeEvent


class ClusterEngine:
    """Rolling-window smart-money cluster detection."""

    def __init__(
        self,
        window_sec: int = 300,
        min_wallets: int = 3,
        min_score: float = 4.0,
    ) -> None:
        self.window_sec = window_sec
        self.min_wallets = min_wallets
        self.min_score = min_score
        self._recent: list[TradeEvent] = []
        self._fired: set[tuple[str, Side, frozenset[str]]] = set()

    def ingest(self, ev: TradeEvent, now: int | None = None) -> ClusterAlert | None:
        if ev.side != Side.BUY:
            return None
        now = now or ev.ts
        self._recent.append(ev)
        cutoff = now - self.window_sec
        self._recent = [e for e in self._recent if e.ts >= cutoff]

        by_mint: dict[str, list[TradeEvent]] = defaultdict(list)
        for e in self._recent:
            if e.side == Side.BUY:
                by_mint[e.mint].append(e)

        best: ClusterAlert | None = None
        for mint, events in by_mint.items():
            wallet_best: dict[str, TradeEvent] = {}
            for e in events:
                cur = wallet_best.get(e.wallet)
                if not cur or e.weight * e.sol_delta > cur.weight * cur.sol_delta:
                    wallet_best[e.wallet] = e
            if len(wallet_best) < self.min_wallets:
                continue
            score = sum(e.weight for e in wallet_best.values())
            if score < self.min_score:
                continue
            key = (mint, Side.BUY, frozenset(wallet_best.keys()))
            if key in self._fired:
                continue
            self._fired.add(key)
            best = ClusterAlert(
                mint=mint,
                wallets=sorted(wallet_best.keys()),
                score=score,
                side=Side.BUY,
                ts=now,
                events=list(wallet_best.values()),
            )
        return best

    def load_events(self, events: list[TradeEvent]) -> list[ClusterAlert]:
        alerts: list[ClusterAlert] = []
        for ev in sorted(events, key=lambda e: e.ts):
            a = self.ingest(ev, now=ev.ts)
            if a:
                alerts.append(a)
        return alerts
