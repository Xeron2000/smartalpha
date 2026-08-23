from __future__ import annotations

import time
from typing import Any

import httpx

from smartalpha.config import Settings

# GMGN official OpenAPI base — same gmgn.ai host, auth via header instead of cookie.
# Real endpoints mirror the cookie paths; api-key is sent as header and query fallback.
GMGN_BASE = "https://gmgn.ai"
GMGN_RANK_PATH = "/defi/quotation/v1/rank/sol/swaps/24h"
GMGN_NEW_PATH = "/defi/quotation/v1/pairs/sol/new_pairs/24h"
GMGN_KLINE_PATH = "/defi/quotation/v1/tokens/kline"
GMGN_TOKEN_PATH = "/defi/quotation/v1/tokens/sol/{mint}"
GMGN_POOL_PATH = "/defi/quotation/v1/pairs/sol/{mint}"
GMGN_SECURITY_PATH = "/defi/quotation/v1/tokens/sol/security/{mint}"


class MissingGMGNKeyError(RuntimeError):
    pass


def _api_key(settings: Settings | None = None) -> str:
    s = settings or Settings()
    key = (s.gmgn_api_key or "").strip()
    if not key:
        raise MissingGMGNKeyError("GMGN_API_KEY not set — add to .env")
    return key


def _headers(settings: Settings | None = None) -> dict[str, str]:
    key = _api_key(settings)
    return {
        "Authorization": f"Bearer {key}",
        "X-API-KEY": key,
        "Accept": "application/json",
        "User-Agent": "smartalpha/0.1",
        "Referer": "https://gmgn.ai/",
        "Origin": "https://gmgn.ai",
    }


def _fetch_json(path: str, params: dict[str, Any] | None = None, *, settings: Settings | None = None) -> dict | None:
    s = settings or Settings()
    headers = _headers(s)
    # also send as query for providers that expect ?api-key=
    q = dict(params or {})
    # prefer header, but include query fallback
    if "api-key" not in q and "apikey" not in q:
        q["api-key"] = _api_key(s)
    url = f"{GMGN_BASE}{path}"
    with httpx.Client(timeout=20.0, headers=headers) as client:
        r = client.get(url, params=q)
        if r.status_code in (401, 403):
            raise MissingGMGNKeyError(f"GMGN auth failed {r.status_code}: {r.text[:200]}")
        if r.status_code != 200:
            return None
        if r.headers.get("content-type", "").startswith("text/html"):
            return None
        try:
            return r.json()
        except Exception:
            return None


def _envelope(data: Any, source: str = "gmgn") -> dict:
    return {"data": data, "source": source, "observed_at": int(time.time())}


# --- public API ---

def get_candidates_rank(limit: int = 20, settings: Settings | None = None) -> dict | None:
    payload = _fetch_json(GMGN_RANK_PATH, {"orderby": "priceChange", "direction": "desc", "limit": limit}, settings=settings)
    if payload is None:
        return None
    return _envelope(payload, "gmgn")


def get_new_pairs(limit: int = 20, settings: Settings | None = None) -> dict | None:
    payload = _fetch_json(GMGN_NEW_PATH, {"limit": limit, "platforms[]": "pump"}, settings=settings)
    if payload is None:
        return None
    return _envelope(payload, "gmgn")


def get_kline(mint: str, interval: str = "1m", limit: int = 100, settings: Settings | None = None) -> dict | None:
    # interval: 30s | 1m (official). Try both path styles. Returns envelope or None.
    # ponytail: if no API key, return None quickly to allow Dex fallback
    try:
        _api_key(settings)
    except MissingGMGNKeyError:
        return None
    for path in (GMGN_KLINE_PATH, f"/defi/quotation/v1/tokens/kline/{mint}"):
        try:
            payload = _fetch_json(path, {"token": mint, "mint": mint, "interval": interval, "limit": limit, "chain": "sol"}, settings=settings)
        except MissingGMGNKeyError:
            return None
        if payload and payload.get("code") in (0, None):
            data = payload.get("data")
            if isinstance(data, list) and data:
                return _envelope({"kline": data, "interval": interval}, "gmgn")
            if isinstance(data, dict) and data:
                # data may be {kline: [...]} or {list: [...]}
                inner = data.get("kline") or data.get("list") or data.get("data") or data
                if isinstance(inner, list) and inner:
                    return _envelope({"kline": inner, "interval": interval}, "gmgn")
                if isinstance(data, dict) and data:
                    return _envelope(data, "gmgn")
        try:
            payload2 = _fetch_json(path, {"address": mint, "interval": interval, "limit": limit}, settings=settings)
        except MissingGMGNKeyError:
            return None
        if payload2 and payload2.get("code") in (0, None):
            data = payload2.get("data")
            if data:
                inner = data.get("kline") if isinstance(data, dict) else None
                kl = inner if isinstance(inner, list) else (data if isinstance(data, list) else None)
                if kl:
                    return _envelope({"kline": kl, "interval": interval}, "gmgn")
    return None


def _extract_close(candle: Any) -> float | None:
    if isinstance(candle, dict):
        for k in ("close", "c", "close_price", "price"):
            if k in candle and candle[k] is not None:
                try:
                    return float(candle[k])
                except Exception:
                    continue
        # GMGN sometimes: {o,h,l,c,v,t}
        if "c" in candle:
            try:
                return float(candle["c"])
            except Exception:
                pass
    elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
        # [time, open, high, low, close, volume]
        try:
            return float(candle[4])
        except Exception:
            pass
    return None


def _extract_high_low(candle: Any) -> tuple[float | None, float | None]:
    if isinstance(candle, dict):
        h = candle.get("high") or candle.get("h")
        lo = candle.get("low") or candle.get("l")
        try:
            return (float(h) if h is not None else None, float(lo) if lo is not None else None)
        except Exception:
            return (None, None)
    elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
        try:
            return (float(candle[2]), float(candle[3]))
        except Exception:
            return (None, None)
    return (None, None)


def kline_to_gains(kline: list[Any], interval: str) -> dict | None:
    """Entry = first close; compute % gains at 90/180/300/900 + MFE/MAE."""
    if not kline or len(kline) < 2:
        return None
    entry = _extract_close(kline[0])
    if not entry or entry == 0:
        return None
    # map interval -> seconds per candle
    sec = 30 if interval == "30s" else 60
    targets = {"90": 90, "180": 180, "300": 300, "900": 900}
    out: dict[str, float] = {}
    for key, secs in targets.items():
        idx = max(0, round(secs / sec))
        # clamp to available length (if mint too young)
        if idx >= len(kline):
            continue
        close = _extract_close(kline[idx])
        if close is not None:
            out[f"gain_{key}_pct"] = (close - entry) / entry * 100
            out[f"price_{key}"] = close
    # MFE/MAE over full window
    highs: list[float] = []
    lows: list[float] = []
    for c in kline:
        h, lo = _extract_high_low(c)
        if h is not None:
            highs.append(h)
        if lo is not None:
            lows.append(lo)
    if highs:
        out["mfe_pct"] = (max(highs) - entry) / entry * 100
    if lows:
        out["mae_pct"] = (min(lows) - entry) / entry * 100
    # also compute h1/h6/h24 approximations from kline if possible
    # 1h = 3600s, 6h=21600s, 24h=86400s — need enough candles
    for label, secs in (("h1", 3600), ("h6", 21600), ("h24", 86400)):
        idx = round(secs / sec)
        if idx < len(kline):
            close = _extract_close(kline[idx])
            if close is not None:
                out[f"gain_{label}_pct"] = (close - entry) / entry * 100
    out["entry_price"] = entry
    out["kline_interval"] = interval
    out["kline_len"] = len(kline)
    return out


def get_kline_gains(mint: str, settings: Settings | None = None) -> dict | None:
    """Try 30s then 1m Kline, return gains dict with source/observed_at or None."""
    for interval in ("30s", "1m"):
        env = get_kline(mint, interval=interval, limit=200, settings=settings)
        if not env:
            continue
        kline = None
        data = env.get("data") or {}
        if isinstance(data, dict):
            kline = data.get("kline") or data.get("list") or data.get("data")
            if not isinstance(kline, list):
                # maybe data itself is list wrapped
                kline = data if isinstance(data, list) else None
        elif isinstance(data, list):
            kline = data
        if not kline:
            continue
        gains = kline_to_gains(kline, interval)
        if not gains:
            continue
        # merge envelope meta
        gains["source"] = "gmgn"
        gains["observed_at"] = env.get("observed_at", int(time.time()))
        gains["interval"] = interval
        return gains
    return None


def get_token(mint: str, settings: Settings | None = None) -> dict | None:
    path = GMGN_TOKEN_PATH.format(mint=mint)
    payload = _fetch_json(path, settings=settings)
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_pool(mint: str, settings: Settings | None = None) -> dict | None:
    path = GMGN_POOL_PATH.format(mint=mint)
    payload = _fetch_json(path, settings=settings)
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_security(mint: str, settings: Settings | None = None) -> dict | None:
    path = GMGN_SECURITY_PATH.format(mint=mint)
    payload = _fetch_json(path, settings=settings)
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_holders_traders(mint: str, settings: Settings | None = None) -> dict | None:
    # holders/traders often under same token endpoint with type param
    payload = _fetch_json(f"/defi/quotation/v1/tokens/sol/{mint}/holders", {"limit": 50}, settings=settings)
    if payload:
        return _envelope(payload.get("data") or payload, "gmgn")
    return None


def test_api_key(settings: Settings | None = None) -> tuple[bool, str]:
    try:
        _api_key(settings)
    except MissingGMGNKeyError as exc:
        return False, str(exc)
    try:
        # Use rank limit 1 as liveness probe (official equivalent of cookie test)
        payload = _fetch_json(GMGN_RANK_PATH, {"orderby": "priceChange", "direction": "desc", "limit": 1}, settings=settings)
    except MissingGMGNKeyError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"network error: {exc}"
    if not payload:
        return False, "GMGN empty or blocked"
    if payload.get("code") not in (0, None):
        return False, f"GMGN error: {payload.get('message') or payload.get('reason') or payload}"
    return True, "GMGN API OK"


# ponytail: low-level fetch exposed for mint_sources migration, returns raw payload
def fetch_json(path: str, params: dict[str, Any] | None = None, settings: Settings | None = None) -> dict | None:
    return _fetch_json(path, params, settings=settings)
