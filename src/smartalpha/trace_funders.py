from __future__ import annotations

from dataclasses import dataclass, field

from smartalpha.config import Settings
from smartalpha.funder import resolve_first_funder
from smartalpha.launch_intel import analyze_launch
from smartalpha.rpc import SolanaRpc


@dataclass
class FunderHit:
    funder: str
    count: int
    buyers: list[str] = field(default_factory=list)
    is_bundler_signal: bool = False


@dataclass
class TraceReport:
    mint: str
    buyers_traced: int
    funders: list[FunderHit]
    bundler_wallets: list[str]
    suggested_hot: list[dict]
    notes: list[str] = field(default_factory=list)


def trace_mint_funders(
    mint: str,
    rpc: SolanaRpc,
    *,
    pair_address: str | None = None,
    max_buyers: int = 20,
    settings: Settings | None = None,
) -> TraceReport:
    intel = analyze_launch(mint, rpc, pair_address=pair_address, settings=settings)
    buyers = intel.buyers[:max_buyers]
    notes = list(intel.notes)

    funder_to_buyers: dict[str, list[str]] = {}
    for b in buyers:
        if b.wallet in intel.bundler_wallets:
            continue
        funder, _src = resolve_first_funder(rpc, b.wallet, settings)
        if not funder:
            notes.append(f"no funder found for {b.wallet[:8]}...")
            continue
        funder_to_buyers.setdefault(funder, []).append(b.wallet)

    hits: list[FunderHit] = []
    for funder, ws in funder_to_buyers.items():
        hits.append(
            FunderHit(
                funder=funder,
                count=len(ws),
                buyers=ws,
                is_bundler_signal=len(ws) >= 3,
            )
        )
    hits.sort(key=lambda h: (-h.count, h.funder))

    suggested = []
    for h in hits:
        if h.is_bundler_signal:
            continue  # likely deployer bundle funder, not alpha
        suggested.append(
            {
                "address": h.funder,
                "label": f"from-mint-{mint[:8]}",
                "weight": min(1.0 + h.count * 0.2, 2.0),
                "buyer_count": h.count,
            }
        )

    return TraceReport(
        mint=mint,
        buyers_traced=len(buyers),
        funders=hits,
        bundler_wallets=intel.bundler_wallets,
        suggested_hot=suggested[:10],
        notes=notes,
    )
