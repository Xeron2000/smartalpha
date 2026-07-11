from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from smartalpha.config import Settings
from smartalpha.rpc import SolanaRpc


@dataclass(frozen=True)
class HotFunder:
    address: str
    label: str = ""
    weight: float = 1.0


# ponytail: no more persistent funders.json. Use HotFunder for session-injected funders only.


def wallet_age_hours(rpc: SolanaRpc, wallet: str, *, max_pages: int = 3) -> float | None:
    """Age since oldest fetched signature. ponytail: capped pagination, not full history."""
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


def find_sol_funder(rpc: SolanaRpc, wallet: str, *, scan: int = 15) -> str | None:
    """Recent incoming SOL (fast, may miss first funder)."""
    sigs = rpc.get_signatures(wallet, limit=scan)
    for s in sigs:
        tx = rpc.get_transaction(s["signature"])
        if not tx:
            continue
        funder = _incoming_sol_source(tx, wallet)
        if funder:
            return funder
    return None


def helius_funded_by(wallet: str, api_key: str) -> str | None:
    url = f"https://api.helius.xyz/v1/wallet/{wallet}/funded-by"
    with httpx.Client(timeout=20.0) as client:
        r = client.get(url, params={"api-key": api_key})
        if r.status_code != 200:
            return None
        data = r.json()
    # { "funder": "...", ... } or nested data
    if isinstance(data, dict):
        for key in ("funder", "fundedBy", "funded_by"):
            v = data.get(key)
            if isinstance(v, str) and len(v) >= 32:
                return v
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("funder", "fundedBy", "funded_by"):
                v = inner.get(key)
                if isinstance(v, str) and len(v) >= 32:
                    return v
    return None


def solscan_funded_by(wallet: str, api_key: str) -> str | None:
    url = "https://pro-api.solscan.io/v2.0/account/funded_by"
    headers = {"token": api_key}
    with httpx.Client(timeout=20.0, headers=headers) as client:
        r = client.get(url, params={"address": wallet})
        if r.status_code != 200:
            return None
        data = r.json()
    block = data.get("data") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return None
    for key in ("funder", "funded_by", "fund_by"):
        v = block.get(key)
        if isinstance(v, str) and len(v) >= 32:
            return v
    return None


def resolve_first_funder(
    rpc: SolanaRpc,
    wallet: str,
    settings: Settings | None = None,
) -> tuple[str | None, str]:
    """Priority: Helius → Solscan Pro → public RPC."""
    settings = settings or Settings()
    if settings.helius_key:
        f = helius_funded_by(wallet, settings.helius_key)
        if f:
            return f, "helius"
    if settings.solscan_key:
        f = solscan_funded_by(wallet, settings.solscan_key)
        if f:
            return f, "solscan"
    f = find_first_funder(rpc, wallet)
    return f, "rpc"


def find_first_funder(rpc: SolanaRpc, wallet: str, *, max_sigs: int = 300) -> str | None:
    """First native SOL transfer to wallet (Solscan 'Funded by' equivalent via RPC)."""
    sigs: list[dict] = []
    before: str | None = None
    while len(sigs) < max_sigs:
        batch = rpc.get_signatures(wallet, before=before, limit=100)
        if not batch:
            break
        sigs.extend(batch)
        before = batch[-1]["signature"]
        if len(batch) < 100:
            break

    if not sigs:
        return None

    # oldest first
    ordered = sorted(
        [s for s in sigs if s.get("blockTime")],
        key=lambda s: s["blockTime"],
    )
    for s in ordered[:30]:
        tx = rpc.get_transaction(s["signature"])
        funder = _incoming_sol_source(tx, wallet) if tx else None
        if funder:
            return funder
    return None


def _incoming_sol_source(tx: dict[str, Any], wallet: str) -> str | None:
    meta = tx.get("meta") or {}
    keys = tx["transaction"]["message"]["accountKeys"]
    addrs = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    try:
        idx = addrs.index(wallet)
    except ValueError:
        return None
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if idx >= len(pre) or idx >= len(post):
        return None
    gain = post[idx] - pre[idx]
    if gain <= 0:
        return None
    # find largest debited non-wallet account
    best_i, best_loss = -1, 0
    for i, (a, b) in enumerate(zip(pre, post, strict=False)):
        if i == idx:
            continue
        loss = a - b
        if loss > best_loss:
            best_loss, best_i = loss, i
    if best_i >= 0 and best_loss > 0:
        return addrs[best_i]
    return None


def dex_pair_address(mint: str) -> str | None:
    meta = dex_pair_meta(mint)
    return meta.get("pair_address") if meta else None


def dex_pair_meta(mint: str) -> dict | None:
    """Top-volume SOL pair metadata from DexScreener."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url)
        if r.status_code != 200:
            return None
        pairs = r.json().get("pairs") or []
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        return None
    sol.sort(key=lambda p: (p.get("volume", {}) or {}).get("h24") or 0, reverse=True)
    top = sol[0]
    pc = top.get("priceChange") or {}
    liq = (top.get("liquidity") or {}).get("usd")
    created = top.get("pairCreatedAt")
    out: dict = {
        "pair_address": top.get("pairAddress"),
        "price_usd": float(top["priceUsd"]) if top.get("priceUsd") is not None else None,
        "liquidity_usd": float(liq) if liq is not None else None,
        "pair_created_at_ms": int(created) if created else None,
    }
    for k in ("m5", "h1", "h6", "h24"):
        v = pc.get(k)
        if v is not None:
            out[f"gain_{k}_pct"] = float(v)
    return out


def dex_price_snapshot(mint: str) -> dict | None:
    meta = dex_pair_meta(mint)
    if not meta:
        return None
    return {
        "price_usd": meta.get("price_usd"),
        "liquidity_usd": meta.get("liquidity_usd"),
        "gain_m5_pct": meta.get("gain_m5_pct"),
        "gain_h1_pct": meta.get("gain_h1_pct"),
        "gain_h6_pct": meta.get("gain_h6_pct"),
        "gain_h24_pct": meta.get("gain_h24_pct"),
        "ts": int(time.time()),
    }


def dex_pair_created_at(mint: str) -> int | None:
    meta = dex_pair_meta(mint)
    if not meta:
        return None
    ms = meta.get("pair_created_at_ms")
    return int(ms // 1000) if ms else None


def dex_token_outcome(mint: str) -> dict[str, float] | None:
    """DexScreener priceChange + liquidity on top-volume SOL pair."""
    meta = dex_pair_meta(mint)
    if not meta:
        return None
    out: dict[str, float] = {}
    for k in ("h1", "h6", "h24"):
        v = meta.get(f"gain_{k}_pct")
        if v is not None:
            out[k] = float(v)
    if meta.get("liquidity_usd") is not None:
        out["liquidity_usd"] = float(meta["liquidity_usd"])
    if meta.get("price_usd") is not None:
        out["price_usd"] = float(meta["price_usd"])
    created_ms = meta.get("pair_created_at_ms")
    if created_ms:
        out["pair_age_hours"] = max(0.0, (time.time() - created_ms / 1000) / 3600)
    return out or None
