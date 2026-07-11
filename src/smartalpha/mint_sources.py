from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field

import httpx

from smartalpha.config import Settings

DEX_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GMGN_RANK = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/24h"
GMGN_NEW = "https://gmgn.ai/defi/quotation/v1/pairs/sol/new_pairs/24h"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _gmgn_headers(cookie: str) -> dict[str, str]:
    # ponytail: sid + __cf_bm is enough; cf_clearance optional
    return {
        **_BROWSER_HEADERS,
        "Cookie": cookie.strip(),
        "Referer": "https://gmgn.ai/",
        "Origin": "https://gmgn.ai",
    }


def _gmgn_fetch_json(url: str, cookie: str) -> dict | None:
    """GMGN sits behind Cloudflare; curl passes, httpx often gets 403."""
    headers = _gmgn_headers(cookie)
    cmd = ["curl", "-sS", "-m", "20"]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.append(url)
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not out.strip() or out.lstrip().startswith("<!"):
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


@dataclass
class CandidateMint:
    mint: str
    source: str
    gain_h24_pct: float | None = None
    pair: str | None = None
    dex: str | None = None
    url: str | None = None
    notes: list[str] = field(default_factory=list)


def find_candidate_mints(
    settings: Settings | None = None,
    *,
    min_gain_pct: float = 300.0,
    limit: int = 20,
) -> tuple[list[CandidateMint], list[str]]:
    """Priority: GMGN (cookie) → DexScreener → GeckoTerminal."""
    settings = settings or Settings()
    notes: list[str] = []
    by_mint: dict[str, CandidateMint] = {}

    for name, fn in (
        ("gmgn", lambda: _from_gmgn(settings, min_gain_pct, limit)),
        ("dexscreener", lambda: _from_dexscreener(min_gain_pct, limit)),
        ("geckoterminal", lambda: _from_geckoterminal(min_gain_pct, limit)),
    ):
        try:
            batch = fn()
        except Exception as exc:
            notes.append(f"{name}: failed ({exc})")
            continue
        if not batch:
            notes.append(f"{name}: no matches")
            continue
        notes.append(f"{name}: {len(batch)} candidates")
        for c in batch:
            prev = by_mint.get(c.mint)
            if prev is None or (c.gain_h24_pct or 0) > (prev.gain_h24_pct or 0):
                by_mint[c.mint] = c

    ranked = sorted(
        by_mint.values(),
        key=lambda c: (-(c.gain_h24_pct or 0), c.mint),
    )[:limit]
    return ranked, notes


def _from_gmgn(settings: Settings, min_gain_pct: float, limit: int) -> list[CandidateMint]:
    from smartalpha.gmgn_cookie import ensure_cookie

    cookie = ensure_cookie(settings)
    if not cookie:
        return []

    out: list[CandidateMint] = []
    seen: set[str] = set()

    for url, tag in (
        (f"{GMGN_RANK}?orderby=priceChange&direction=desc&limit={limit * 3}", "rank"),
        (f"{GMGN_NEW}?limit={limit * 3}&platforms[]=pump", "new_pairs"),
    ):
        payload = _gmgn_fetch_json(url, cookie)
        if not payload or payload.get("code") not in (0, None):
            continue
        data = payload.get("data") or {}
        rows = data.get("rank") or data.get("pairs") or []
        for row in rows:
            mint = _gmgn_mint(row)
            if not mint or mint in seen:
                continue
            gain = _gmgn_gain(row)
            launchpad = (row.get("launchpad") or row.get("launchpad_platform") or "").lower()
            if tag == "new_pairs":
                # ungraduated mints rarely have traceable early buyers on DEX pair
                if not _is_pump_mint(mint) or gain is None:
                    continue
            elif not _is_pump_mint(mint) and "pump" not in launchpad:
                continue
            if gain is not None and gain < min_gain_pct:
                continue
            seen.add(mint)
            note = []
            creator_funder = row.get("fund_from_address")
            if not creator_funder:
                creator_funder = (row.get("base_token_info") or {}).get("fund_from_address")
            if creator_funder:
                note.append(f"creator_funder={creator_funder[:8]}")
            out.append(
                CandidateMint(
                    mint=mint,
                    source=f"gmgn:{tag}",
                    gain_h24_pct=gain,
                    dex=row.get("exchange") or row.get("launchpad_platform"),
                    url=f"https://gmgn.ai/sol/token/{mint}",
                    notes=note,
                )
            )
            if len(out) >= limit:
                return out
    return out


def _from_dexscreener(min_gain_pct: float, limit: int) -> list[CandidateMint]:
    seeds: list[tuple[str, str]] = []
    with httpx.Client(timeout=20.0, headers=_BROWSER_HEADERS) as client:
        for path in ("/token-boosts/top/v1", "/token-profiles/latest/v1"):
            r = client.get(f"{DEX_BASE}{path}")
            r.raise_for_status()
            for row in r.json():
                if row.get("chainId") != "solana":
                    continue
                mint = row.get("tokenAddress")
                if mint and _is_pump_mint(mint):
                    seeds.append((mint, row.get("url")))

    # ponytail: dedupe seeds, cap enrichment calls (~300 rpm DexScreener)
    seen: set[str] = set()
    unique: list[tuple[str, str | None]] = []
    for mint, url in seeds:
        if mint in seen:
            continue
        seen.add(mint)
        unique.append((mint, url))
        if len(unique) >= limit * 3:
            break

    out: list[CandidateMint] = []
    with httpx.Client(timeout=20.0, headers=_BROWSER_HEADERS) as client:
        for mint, seed_url in unique:
            if len(out) >= limit:
                break
            c = _enrich_dexscreener_mint(client, mint, seed_url, min_gain_pct)
            if c:
                out.append(c)
            time.sleep(0.25)
    return out


def _enrich_dexscreener_mint(
    client: httpx.Client,
    mint: str,
    seed_url: str | None,
    min_gain_pct: float,
) -> CandidateMint | None:
    r = client.get(f"{DEX_BASE}/latest/dex/tokens/{mint}")
    if r.status_code != 200:
        return None
    pairs = r.json().get("pairs") or []
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        return None
    sol.sort(
        key=lambda p: (
            -(_float((p.get("priceChange") or {}).get("h24")) or 0.0),
            p.get("pairCreatedAt") or 0,
        )
    )
    best = sol[0]
    gain = (best.get("priceChange") or {}).get("h24")
    if gain is None or float(gain) < min_gain_pct:
        return None
    dex = best.get("dexId") or ""
    if not _is_pump_pair(mint, dex):
        return None
    liq = (best.get("liquidity") or {}).get("usd") or 0
    if liq and float(liq) < 1000:
        return None
    return CandidateMint(
        mint=mint,
        source="dexscreener",
        gain_h24_pct=float(gain),
        pair=best.get("pairAddress"),
        dex=dex,
        url=best.get("url") or seed_url,
    )


def _from_geckoterminal(min_gain_pct: float, limit: int) -> list[CandidateMint]:
    out: list[CandidateMint] = []
    with httpx.Client(timeout=20.0, headers=_BROWSER_HEADERS) as client:
        for page in (1, 2):
            if len(out) >= limit:
                break
            r = client.get(f"{GECKO_BASE}/networks/solana/trending_pools?page={page}")
            r.raise_for_status()
            for row in r.json().get("data") or []:
                attrs = row.get("attributes") or {}
                rel = row.get("relationships") or {}
                token_id = (rel.get("base_token") or {}).get("data", {}).get("id") or ""
                mint = token_id.split("_", 1)[-1] if "_" in token_id else token_id
                if not mint or not _is_pump_mint(mint):
                    continue
                dex_id = (rel.get("dex") or {}).get("data", {}).get("id") or ""
                if "pump" not in dex_id and not mint.endswith("pump"):
                    continue
                pc = attrs.get("price_change_percentage") or {}
                gain = _float(pc.get("h24"))
                if gain is None or gain < min_gain_pct:
                    continue
                out.append(
                    CandidateMint(
                        mint=mint,
                        source="geckoterminal",
                        gain_h24_pct=gain,
                        pair=attrs.get("address"),
                        dex=dex_id,
                        url=f"https://www.geckoterminal.com/solana/pools/{attrs.get('address')}",
                    )
                )
                if len(out) >= limit:
                    break
            time.sleep(0.3)
    return out[:limit]


def _gmgn_mint(row: dict) -> str | None:
    bti = row.get("base_token_info") or {}
    return (
        row.get("address")
        or row.get("mint")
        or row.get("token_address")
        or row.get("base_address")
        or bti.get("address")
    )


def _gmgn_gain(row: dict) -> float | None:
    bti = row.get("base_token_info") or {}
    return _float(
        row.get("price_change_percent")
        or row.get("price_change_24h")
        or bti.get("price_change_percent")
        or row.get("price_change_percent1h")
    )


def _is_pump_mint(mint: str) -> bool:
    return mint.endswith("pump") or mint.endswith("PUMP")


def _is_pump_pair(mint: str, dex: str) -> bool:
    if _is_pump_mint(mint):
        return True
    return "pump" in (dex or "").lower()


def _float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
