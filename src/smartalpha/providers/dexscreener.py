from __future__ import annotations

import time
from typing import Any

import httpx

DEX_BASE = "https://api.dexscreener.com"

_HEADERS = {"Accept": "application/json", "User-Agent": "smartalpha/0.1"}


def get_pair_meta(mint: str) -> dict | None:
    url = f"{DEX_BASE}/latest/dex/tokens/{mint}"
    with httpx.Client(timeout=15.0, headers=_HEADERS) as client:
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
    volume = top.get("volume") or {}
    txns = top.get("txns") or {}
    liq = (top.get("liquidity") or {}).get("usd")
    created = top.get("pairCreatedAt")
    out: dict[str, Any] = {
        "pair_address": top.get("pairAddress"),
        "price_usd": float(top["priceUsd"]) if top.get("priceUsd") is not None else None,
        "liquidity_usd": float(liq) if liq is not None else None,
        "pair_created_at_ms": int(created) if created else None,
        "source": "dexscreener",
        "observed_at": int(time.time()),
    }
    for k in ("m5", "h1", "h6", "h24"):
        v = pc.get(k)
        if v is not None:
            out[f"gain_{k}_pct"] = float(v)
        v = volume.get(k)
        if v is not None:
            out[f"volume_{k}_usd"] = float(v)
        counts = txns.get(k) or {}
        if counts.get("buys") is not None:
            out[f"buys_{k}"] = int(counts["buys"])
        if counts.get("sells") is not None:
            out[f"sells_{k}"] = int(counts["sells"])
    return out
