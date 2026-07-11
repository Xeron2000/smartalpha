from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from smartalpha.config import ROOT, Settings
from smartalpha.rpc import SolanaRpc
from smartalpha.trace_funders import trace_mint_funders

# Known Solana CEX hot wallets — cross-mint hits are deposit noise, not alpha
KNOWN_CEX_FUNDERS = frozenset({
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",  # Binance
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ",  # Coinbase
    "2ojv9BAiHUrvsm9gxDeR1J58A26uGH6G6HpR946FebGp",  # Coinbase 2
    "GJRs4FwHtemZ5HZ9Afify24Bvoj8doNEMAmQ1DoepR",   # OKX
})
CEX_FUNDER_HINTS = (
    "binance",
    "coinbase",
    "okx",
    "kraken",
    "bybit",
    "kucoin",
    "gate.io",
    "mexc",
    "hot wallet",
    "withdraw",
)


@dataclass
class FunderAggregate:
    address: str
    mints: set[str] = field(default_factory=set)
    buyers: list[str] = field(default_factory=list)
    bundler_mints: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def mint_count(self) -> int:
        return len(self.mints)

    @property
    def score(self) -> float:
        """Cross-mint repeats matter most."""
        if self.bundler_mints and len(self.bundler_mints) == len(self.mints):
            return 0.0
        base = self.mint_count * 10 + len(set(self.buyers))
        if self.bundler_mints:
            base *= 0.3
        return base


@dataclass
class DiscoverReport:
    mints_processed: int
    funders: list[FunderAggregate]
    recommended: list[dict]
    skipped_mints: list[str] = field(default_factory=list)


def load_mint_list(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # allow "mint # comment" or full line mint
        mint = line.split()[0]
        if len(mint) >= 32:
            out.append(mint)
    return out


def discover_funders(
    mints: list[str],
    rpc: SolanaRpc,
    *,
    max_buyers: int = 15,
    min_mint_hits: int = 2,
    settings: Settings | None = None,
) -> DiscoverReport:
    agg: dict[str, FunderAggregate] = {}
    skipped: list[str] = []

    for mint in mints:
        try:
            report = trace_mint_funders(
                mint, rpc, max_buyers=max_buyers, settings=settings
            )
        except Exception as exc:
            skipped.append(f"{mint[:12]}... ({exc})")
            continue
        if not report.funders:
            skipped.append(f"{mint[:12]}... (no funders traced)")
            continue

        for hit in report.funders:
            fa = agg.setdefault(hit.funder, FunderAggregate(address=hit.funder))
            fa.mints.add(mint)
            fa.buyers.extend(hit.buyers)
            if hit.is_bundler_signal:
                fa.bundler_mints.add(mint)

        time.sleep(0.5)  # ponytail: gentle RPC pacing between mints

    ranked = sorted(agg.values(), key=lambda f: -f.score)
    recommended: list[dict] = []
    for fa in ranked:
        if fa.mint_count < min_mint_hits:
            continue
        if fa.score <= 0:
            fa.notes.append("bundler-only pattern")
            continue
        if fa.address in KNOWN_CEX_FUNDERS:
            fa.notes.append("CEX hot wallet — skipped")
            continue
        if _looks_like_cex(fa.address):
            fa.notes.append("verify: may be CEX hot wallet — check Solscan label")
        recommended.append(
            {
                "address": fa.address,
                "label": f"cross-{fa.mint_count}-mints",
                "weight": min(1.0 + fa.mint_count * 0.25, 2.5),
                "mint_count": fa.mint_count,
                "buyer_count": len(set(fa.buyers)),
                "mints": sorted(fa.mints),
                "solscan": f"https://solscan.io/account/{fa.address}",
            }
        )

    return DiscoverReport(
        mints_processed=len(mints) - len([s for s in skipped if "no funders" in s]),
        funders=ranked,
        recommended=recommended,
        skipped_mints=skipped,
    )


def _looks_like_cex(address: str) -> bool:
    # on-chain we can't know label; flag only if user pasted labeled notes in mints file
    return False


def write_discover_report(report: DiscoverReport, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "funder_discovery.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mints_processed": report.mints_processed,
        "recommended": report.recommended,
        "skipped_mints": report.skipped_mints,
        "all_funders": [
            {
                "address": fa.address,
                "mint_count": fa.mint_count,
                "score": fa.score,
                "mints": sorted(fa.mints),
                "bundler_mints": sorted(fa.bundler_mints),
                "notes": fa.notes,
                "solscan": f"https://solscan.io/account/{fa.address}",
            }
            for fa in report.funders[:50]
        ],
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p
