import time
from dataclasses import dataclass, field

from smartalpha.config import Settings
from smartalpha.providers.dexscreener import dex_pair_address
from smartalpha.rpc import SolanaRpc


def wallet_age_hours(rpc: SolanaRpc, wallet: str, *, max_pages: int = 3) -> float | None:
    """Age since oldest fetched signature."""
    oldest: int | None = None
    before: str | None = None
    for _ in range(max_pages):
        batch = rpc.get_signatures(wallet, before=before, limit=100)
        if not batch:
            break
        for s in batch:
            bt = s.get("blockTime")
            if bt:
                oldest = bt if oldest is None else min(oldest, bt)
        before = batch[-1]["signature"]
        if len(batch) < 100:
            break
    if oldest is None:
        return None
    return max(0.0, (time.time() - oldest) / 3600)


def wallet_age_hours_at(
    rpc: SolanaRpc, wallet: str, as_of_ts: int, *, max_pages: int = 5
) -> tuple[float | None, bool]:
    """Historical wallet age at as_of_ts."""
    oldest: int | None = None
    before: str | None = None
    complete = True
    for i in range(max_pages):
        batch = rpc.get_signatures(wallet, before=before, limit=100)
        if not batch:
            break
        for s in batch:
            bt = s.get("blockTime")
            if bt is None or bt > as_of_ts:
                continue
            oldest = bt if oldest is None else min(oldest, bt)
        if len(batch) < 100:
            complete = True
            break
        if i == max_pages - 1:
            complete = False
        before = batch[-1]["signature"]
    if oldest is None:
        return None, complete
    return max(0.0, (as_of_ts - oldest) / 3600), complete


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
    hot_funders: dict | None = None,
    as_of_ts: int | None = None,
    launch_ts: int | None = None,
) -> LaunchIntel:
    """Behavior-first: score early buyers by age/funder/bundle, not static watchlist.
    When as_of_ts is set, only transactions with launch_ts <= ts <= as_of_ts are considered (historical)."""
    pair = pair_address or dex_pair_address(mint)
    notes: list[str] = []
    if not pair:
        notes.append("no dex pair yet — pre-graduation mint needs gRPC launch feed")

    raw_buys: list[BuyerProfile] = []
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
        # if pagination cap hit and we didn't get full window, mark incomplete
        if not window_complete:
            notes.append("HISTORICAL_INCOMPLETE: pair signatures not fully backfilled to launch")

    # dedupe: first buy per wallet
    first: dict[str, BuyerProfile] = {}
    for b in sorted(raw_buys, key=lambda x: x.ts):
        if b.wallet not in first:
            first[b.wallet] = b
    buyers = list(first.values())

    # enrich + score (historical wallet age when as_of_ts set)
    funder_map: dict[str, str | None] = {}
    for b in buyers:
        if as_of_ts is not None:
            age_res = wallet_age_hours_at(rpc, b.wallet, as_of_ts)
            if isinstance(age_res, tuple):
                age, complete = age_res
                # incomplete age cannot be trusted as fresh
                b.wallet_age_hours = age if complete else None
                if not complete:
                    notes.append(f"wallet {b.wallet[:6]} age incomplete")
            else:
                b.wallet_age_hours = age_res
        else:
            b.wallet_age_hours = wallet_age_hours(rpc, b.wallet)
    bundler_wallets = _detect_bundlers(buyers, funder_map)
    hot_hits: list[str] = []

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
        window_complete=window_complete,
        as_of_ts=as_of_ts,
        launch_ts=launch_ts,
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
