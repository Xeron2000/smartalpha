from __future__ import annotations

import time
from typing import Any

import httpx

DEX_BASE = "https://api.dexscreener.com"

_HEADERS = {"Accept": "application/json", "User-Agent": "smartalpha/0.1"}


def _envelope(data: Any, source: str = "dexscreener") -> dict:
    return {"data": data, "source": source, "observed_at": int(time.time())}


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
    return out


def price_snapshot(mint: str) -> dict | None:
    meta = get_pair_meta(mint)
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
        "source": "dexscreener",
        "observed_at": int(time.time()),
    }


def dex_pair_address(mint: str) -> str | None:
    meta = get_pair_meta(mint)
    return meta.get("pair_address") if meta else None


def dex_token_outcome(mint: str) -> dict | None:
    return get_pair_meta(mint)


def dex_price_snapshot(mint: str) -> dict | None:
    return price_snapshot(mint)
