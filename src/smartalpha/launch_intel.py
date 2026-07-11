from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smartalpha.config import Settings
from smartalpha.funder import wallet_age_hours
from smartalpha.rpc import SolanaRpc

if TYPE_CHECKING:
    from smartalpha.funder import HotFunder


@dataclass
class BuyerProfile:
    wallet: str
    buy_sol: float
    ts: int
    signature: str
    slot: int | None = None
    wallet_age_hours: float | None = None
    funder: str | None = None
    funder_known: bool = False
    flags: list[str] = field(default_factory=list)
    follow_score: float = 0.0


@dataclass
class LaunchIntel:
    mint: str
    buyers: list[BuyerProfile]
    bundler_wallets: list[str]
    hot_funder_hits: list[str]
    copytrap_risk: str
    recommendation: str
    funder_injected: bool = False
    notes: list[str] = field(default_factory=list)


def analyze_launch(
    mint: str,
    rpc: SolanaRpc,
    *,
    pair_address: str | None = None,
    max_sigs: int = 40,
    settings: Settings | None = None,
    hot_funders: dict[str, HotFunder] | None = None,
) -> LaunchIntel:
    """Behavior-first: score early buyers by age/funder/bundle, not static watchlist."""
    from smartalpha.funder import dex_pair_address, resolve_first_funder

    settings_obj = settings or Settings()
    hot = hot_funders if hot_funders is not None else {}
    pair = pair_address or dex_pair_address(mint)
    notes: list[str] = []
    if not pair:
        notes.append("no dex pair yet — pre-graduation mint needs gRPC launch feed")

    raw_buys: list[BuyerProfile] = []
    if pair:
        sigs = rpc.get_signatures(pair, limit=max_sigs)
        for sig_entry in reversed(sigs):
            if sig_entry.get("err"):
                continue
            tx = rpc.get_transaction(sig_entry["signature"])
            if not tx:
                continue
            slot = tx.get("slot")
            ts = int(tx.get("blockTime") or 0)
            for ev in _extract_mint_buys(tx, mint):
                raw_buys.append(
                    BuyerProfile(
                        wallet=ev["wallet"],
                        buy_sol=ev["sol"],
                        ts=ts,
                        signature=sig_entry["signature"],
                        slot=slot,
                    )
                )

    # dedupe: first buy per wallet
    first: dict[str, BuyerProfile] = {}
    for b in sorted(raw_buys, key=lambda x: x.ts):
        if b.wallet not in first:
            first[b.wallet] = b
    buyers = list(first.values())

    # enrich + score
    funder_map: dict[str, str | None] = {}
    for b in buyers:
        b.wallet_age_hours = wallet_age_hours(rpc, b.wallet)
        if hot_funders is not None:
            funder, _src = resolve_first_funder(rpc, b.wallet, settings_obj)
            b.funder = funder
            if b.funder:
                funder_map[b.wallet] = b.funder
                if b.funder in hot:
                    b.funder_known = True

    bundler_wallets = _detect_bundlers(buyers, funder_map if hot_funders is not None else {})
    hot_hits = sorted({b.funder for b in buyers if b.funder and b.funder in hot}) if hot_funders is not None else []

    for b in buyers:
        _score_buyer(b, bundler_wallets)

    copytrap = _copytrap_level(buyers, hot_funders is not None)
    rec = _recommendation(buyers, bundler_wallets, hot_hits)

    return LaunchIntel(
        mint=mint,
        buyers=sorted(buyers, key=lambda x: -x.follow_score),
        bundler_wallets=bundler_wallets,
        hot_funder_hits=hot_hits,
        copytrap_risk=copytrap,
        recommendation=rec,
        funder_injected=hot_funders is not None,
        notes=notes,
    )


def _extract_mint_buys(tx: dict, mint: str) -> list[dict]:
    meta = tx.get("meta") or {}
    pre: dict[tuple[str, str], float] = {}
    post: dict[tuple[str, str], float] = {}
    for side, bag in (("pre", pre), ("post", post)):
        for b in meta.get(f"{side}TokenBalances") or []:
            if b.get("mint") != mint:
                continue
            owner = b.get("owner")
            if not owner:
                continue
            ui = (b.get("uiTokenAmount") or {}).get("uiAmount") or 0
            key = (owner, mint)
            bag[key] = float(ui)

    out: list[dict] = []
    keys = set(pre) | set(post)
    for key in keys:
        delta = post.get(key, 0) - pre.get(key, 0)
        if delta <= 0:
            continue
        owner = key[0]
        # rough SOL proxy from native balance change
        sol = _native_delta(tx, owner)
        out.append({"wallet": owner, "sol": max(abs(sol), 0.01)})
    return out


def _native_delta(tx: dict, wallet: str) -> float:
    meta = tx.get("meta") or {}
    keys = tx["transaction"]["message"]["accountKeys"]
    addrs = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    try:
        i = addrs.index(wallet)
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        return (post[i] - pre[i]) / 1e9
    except (ValueError, IndexError):
        return 0.0


def _detect_bundlers(buyers: list[BuyerProfile], funder_map: dict[str, str | None]) -> list[str]:
    """Same slot + shared funder = bundler cluster."""
    by_slot_funder: dict[tuple[int | None, str], list[str]] = {}
    for b in buyers:
        if b.funder:
            key = (b.slot, b.funder)
            by_slot_funder.setdefault(key, []).append(b.wallet)
    flagged: set[str] = set()
    for (_slot, _f), ws in by_slot_funder.items():
        if len(ws) >= 3:
            flagged.update(ws)
    return sorted(flagged)


def _score_buyer(b: BuyerProfile, bundlers: list[str]) -> None:
    score = 0.0
    if b.wallet in bundlers:
        b.flags.append("bundler_cluster")
        b.follow_score = 0.0
        return

    if b.funder_known and b.funder:
        score += 40
        b.flags.append("hot_funder")

    if b.wallet_age_hours is not None:
        if b.wallet_age_hours < 2:
            b.flags.append("fresh_wallet")
            # ponytail: fresh alone is weak signal, not rewarded
        elif b.wallet_age_hours > 48:
            score += 20
            b.flags.append("aged_wallet")

    if b.buy_sol >= 0.5:
        score += 15
        b.flags.append("meaningful_size")

    b.follow_score = min(score, 100.0)


def _copytrap_level(buyers: list[BuyerProfile], has_hot_funders: bool) -> str:
    bundlers = [b for b in buyers if "bundler_cluster" in b.flags]
    if len(bundlers) >= 3:
        return "high"
    fresh = [b for b in buyers if b.wallet_age_hours is not None and b.wallet_age_hours < 1]
    if len(buyers) == 1 and fresh:
        return "high"
    if len(fresh) >= 4 and not has_hot_funders:
        return "medium"
    return "low"


def _recommendation(
    buyers: list[BuyerProfile],
    bundlers: list[str],
    hot_hits: list[str],
) -> str:
    organic = [b for b in buyers if b.follow_score >= 30 and b.wallet not in bundlers]
    if hot_hits and len(organic) >= 2:
        return "follow_cohort"
    # behavior-only mode: 1 high-confidence buyer is enough
    if len(organic) >= 1:
        return "follow_cohort"
    return "skip"
