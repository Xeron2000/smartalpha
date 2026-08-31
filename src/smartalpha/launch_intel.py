import time
from dataclasses import dataclass, field

from smartalpha.config import Settings
from smartalpha.providers.dexscreener import get_pair_meta
from smartalpha.rpc import SolanaRpc


def wallet_age_hours(
    rpc: SolanaRpc,
    wallet: str,
    *,
    as_of_ts: int | None = None,
    max_pages: int | None = None,
) -> tuple[float | None, bool]:
    """Return wallet age and whether the fetched history is complete."""
    cutoff = as_of_ts or int(time.time())
    pages = max_pages if max_pages is not None else (5 if as_of_ts is not None else 3)
    oldest: int | None = None
    before: str | None = None
    complete = True
    for index in range(pages):
        batch = rpc.get_signatures(wallet, before=before, limit=100)
        if not batch:
            break
        for signature in batch:
            block_time = signature.get("blockTime")
            if block_time is None or block_time > cutoff:
                continue
            oldest = block_time if oldest is None else min(oldest, block_time)
        if len(batch) < 100:
            break
        if index == pages - 1:
            complete = False
        before = batch[-1]["signature"]
    if oldest is None:
        return None, complete
    return max(0.0, (cutoff - oldest) / 3600), complete


@dataclass
class BuyerProfile:
    wallet: str
    buy_sol: float
    ts: int
    signature: str
    slot: int | None = None
    wallet_age_hours: float | None = None


@dataclass
class LaunchIntel:
    mint: str
    buyers: list[BuyerProfile]
    bundler_wallets: list[str]
    copytrap_risk: str
    recommendation: str
    buy_count: int = 0
    sell_count: int = 0
    volume_sol: float = 0.0
    top_buyer_share: float = 0.0
    notes: list[str] = field(default_factory=list)
    window_complete: bool = True
    as_of_ts: int | None = None
    launch_ts: int | None = None


def analyze_launch(
    mint: str,
    rpc: SolanaRpc,
    *,
    pair_address: str | None = None,
    max_sigs: int = 40,
    settings: Settings | None = None,
    as_of_ts: int | None = None,
    launch_ts: int | None = None,
) -> LaunchIntel:
    """Build outcome-blind launch features from the bounded observation window.

    The returned features are bounded by ``launch_ts`` and ``as_of_ts``;
    missing launch timestamps are handled by the caller as non-entry data.
    """
    pair = pair_address or ((get_pair_meta(mint) or {}).get("pair_address"))
    notes: list[str] = []
    if not pair:
        notes.append("no dex pair yet — pre-graduation mint needs gRPC launch feed")

    raw_buys: list[BuyerProfile] = []
    buy_count = 0
    sell_count = 0
    volume_sol = 0.0
    buy_volume_by_wallet: dict[str, float] = {}
    window_complete = True
    if pair:
        # Paginated retrieval to cover launch..as_of window, not just latest 40
        all_sigs: list[dict] = []
        before: str | None = None
        # max_pages to avoid infinite loop, enough to cover 7-day window for active mints
        max_pages_sig = max(10, (max_sigs // 100) + 5)
        for _ in range(max_pages_sig):
            batch = rpc.get_signatures(pair, before=before, limit=100)
            if not batch:
                break
            all_sigs.extend(batch)
            # check if oldest in batch already <= launch or <= as_of lower bound
            oldest_bt = None
            for s in batch:
                bt = s.get("blockTime")
                if bt is not None:
                    oldest_bt = bt if oldest_bt is None else min(oldest_bt, bt)
            before = batch[-1]["signature"]
            if len(batch) < 100:
                break
            # if we have as_of and launch, stop when we have covered launch
            if as_of_ts is not None and launch_ts is not None:
                if oldest_bt is not None and oldest_bt <= launch_ts:
                    break
            elif as_of_ts is not None:
                if oldest_bt is not None and oldest_bt <= as_of_ts - 86400 * 7:
                    break
            if len(all_sigs) >= 1000:
                break
        else:
            # hit max_pages without covering window
            window_complete = False
        # if we hit cap and last batch was full 100, mark incomplete
        if len(all_sigs) >= 100 and all_sigs and len(batch) == 100:
            # we may not have reached launch_ts
            if as_of_ts is not None and launch_ts is not None:
                has_launch = any((s.get("blockTime") or 0) <= launch_ts for s in all_sigs)
                if not has_launch:
                    window_complete = False
        # Outcome-blind: process FULL window, not truncated 120. If window too large, mark incomplete.
        window_sigs = [s for s in all_sigs if (s.get("blockTime") or 0) >= (launch_ts or 0) and (as_of_ts is None or (s.get("blockTime") or 0) <= as_of_ts)] if launch_ts is not None else all_sigs
        MAX_WINDOW_SIGS = 500
        if len(window_sigs) > MAX_WINDOW_SIGS:
            window_complete = False
            notes.append(f"HISTORICAL_INCOMPLETE: window_sigs {len(window_sigs)} > {MAX_WINDOW_SIGS} — early window too large to be complete")
        # process all window_sigs, not truncated
        sigs = window_sigs
        for sig_entry in reversed(sigs):
            if sig_entry.get("err"):
                continue
            tx = rpc.get_transaction(sig_entry["signature"])
            if not tx:
                continue
            slot = tx.get("slot")
            ts = int(tx.get("blockTime") or 0)
            if as_of_ts is not None and launch_ts is not None:
                if ts < launch_ts or ts > as_of_ts:
                    continue
            elif as_of_ts is not None:
                if ts > as_of_ts:
                    continue
            for ev in _extract_mint_flows(tx, mint):
                volume_sol += ev["sol"]
                if ev["side"] == "buy":
                    buy_count += 1
                    buy_volume_by_wallet[ev["wallet"]] = (
                        buy_volume_by_wallet.get(ev["wallet"], 0.0) + ev["sol"]
                    )
                    raw_buys.append(
                        BuyerProfile(
                            wallet=ev["wallet"],
                            buy_sol=ev["sol"],
                            ts=ts,
                            signature=sig_entry["signature"],
                            slot=slot,
                        )
                    )
                else:
                    sell_count += 1
        # if pagination cap hit and we didn't get full window, mark incomplete
        if not window_complete:
            notes.append("HISTORICAL_INCOMPLETE: pair signatures not fully backfilled to launch")

    # dedupe: first buy per wallet
    first: dict[str, BuyerProfile] = {}
    for b in sorted(raw_buys, key=lambda x: x.ts):
        if b.wallet not in first:
            first[b.wallet] = b
    buyers = list(first.values())
    total_buy_volume = sum(buy_volume_by_wallet.values())
    top_buyer_share = (
        max(buy_volume_by_wallet.values(), default=0.0) / total_buy_volume
        if total_buy_volume > 0
        else 0.0
    )

    # Enrich with wallet age at the observation timestamp.
    for b in buyers:
        age, complete = wallet_age_hours(rpc, b.wallet, as_of_ts=as_of_ts)
        # Incomplete history cannot be trusted as a fresh-wallet signal.
        b.wallet_age_hours = age if as_of_ts is None or complete else None
        if as_of_ts is not None and not complete:
            notes.append(f"wallet {b.wallet[:6]} age incomplete")
    bundler_wallets = _detect_bundlers(buyers)
    copytrap = _copytrap_level(buyers, bundler_wallets)
    rec = _recommendation(buyers, bundler_wallets)

    return LaunchIntel(
        mint=mint,
        buyers=sorted(buyers, key=lambda x: -x.buy_sol),
        bundler_wallets=bundler_wallets,
        copytrap_risk=copytrap,
        recommendation=rec,
        buy_count=buy_count,
        sell_count=sell_count,
        volume_sol=volume_sol,
        top_buyer_share=top_buyer_share,
        notes=notes,
        window_complete=window_complete,
        as_of_ts=as_of_ts,
        launch_ts=launch_ts,
    )


def _extract_mint_flows(tx: dict, mint: str) -> list[dict]:
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
            bag[(owner, mint)] = float(ui)

    out: list[dict] = []
    for key in set(pre) | set(post):
        delta = post.get(key, 0) - pre.get(key, 0)
        if delta == 0:
            continue
        owner = key[0]
        # ponytail: native balance delta is a coarse volume proxy; exact DEX event decoding belongs in the provider.
        sol = max(abs(_native_delta(tx, owner)), 0.01)
        out.append({"wallet": owner, "sol": sol, "side": "buy" if delta > 0 else "sell"})
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


def _detect_bundlers(buyers: list[BuyerProfile]) -> list[str]:
    """Flag cohorts that land in the same slot."""
    by_slot: dict[int, list[str]] = {}
    for buyer in buyers:
        if buyer.slot is not None:
            by_slot.setdefault(buyer.slot, []).append(buyer.wallet)
    flagged = {wallet for wallets in by_slot.values() if len(wallets) >= 3 for wallet in wallets}
    return sorted(flagged)


def _copytrap_level(buyers: list[BuyerProfile], bundler_wallets: list[str]) -> str:
    if len(bundler_wallets) >= 3:
        return "high"
    fresh = [buyer for buyer in buyers if buyer.wallet_age_hours is not None and buyer.wallet_age_hours < 1]
    if len(buyers) == 1 and fresh:
        return "high"
    if len(fresh) >= 4:
        return "medium"
    return "low"


def _recommendation(buyers: list[BuyerProfile], bundlers: list[str]) -> str:
    organic = {buyer.wallet for buyer in buyers} - set(bundlers)
    return "follow_flow" if len(organic) >= 3 else "observe"
