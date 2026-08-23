from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from smartalpha.config import Settings

# Official GMGN OpenAPI — https://openapi.gmgn.ai
# Auth: Exist routes use X-APIKEY + timestamp + client_id (query)
# See https://github.com/GMGNAI/gmgn-skills/blob/main/src/client/OpenApiClient.ts
DEFAULT_HOST = "https://openapi.gmgn.ai"

# Official endpoints (from gmgn-market SKILL.md)
KLINE_PATH = "/v1/market/token_kline"
RANK_PATH = "/v1/market/rank"
TRENCHES_PATH = "/v1/trenches"
TOKEN_INFO_PATH = "/v1/token/info"  # generic, may vary
TOKEN_SECURITY_PATH = "/v1/token/security"


class MissingGMGNKeyError(RuntimeError):
    pass


def _api_key(settings: Settings | None = None) -> str:
    s = settings or Settings()
    key = (s.gmgn_api_key or "").strip()
    if not key:
        raise MissingGMGNKeyError("GMGN_API_KEY not set — add to .env")
    return key


def _host(settings: Settings | None = None) -> str:
    s = settings or Settings()
    h = (s.gmgn_api_base or DEFAULT_HOST).strip().rstrip("/")
    # allow legacy https://gmgn.ai -> map to openapi host
    if h == "https://gmgn.ai":
        return DEFAULT_HOST
    return h


def _auth_query() -> dict[str, str]:
    return {
        "timestamp": str(int(time.time())),
        "client_id": str(uuid.uuid4()),
    }


def _headers(settings: Settings | None = None) -> dict[str, str]:
    key = _api_key(settings)
    return {
        "X-APIKEY": key,
        "Accept": "application/json",
        "User-Agent": "smartalpha/0.1",
    }


def _fetch_json(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> dict | None:
    s = settings or Settings()
    headers = _headers(s)
    q = dict(params or {})
    # add auth query (timestamp + client_id)
    q.update(_auth_query())
    url = f"{_host(s)}{path}"
    with httpx.Client(timeout=20.0, headers=headers) as client:
        if method == "POST":
            r = client.post(url, params=q, json=json_body or {})
        else:
            r = client.get(url, params=q)
        if r.status_code in (401, 403):
            raise MissingGMGNKeyError(f"GMGN auth failed {r.status_code}: {r.text[:300]}")
        if r.status_code == 429:
            # rate limited — surface as is, no auto-retry here (caller may retry)
            return None
        if r.status_code != 200:
            return None
        if r.headers.get("content-type", "").startswith("text/html"):
            return None
        try:
            return r.json()
        except Exception:
            return None



def _run_gmgn_cli(args: list[str]) -> dict | None:
    """Run gmgn-cli and return parsed JSON, or None if not available/failed."""
    import json
    import shutil
    import subprocess
    if shutil.which("gmgn-cli") is None:
        return None
    try:
        out = subprocess.check_output(["gmgn-cli", *args, "--raw"], text=True, timeout=20)
        # gmgn-cli --raw outputs JSON object or array
        try:
            data = json.loads(out)
            # kline: {"list": [...]}  trending: {"list": ...} trenches: {...}
            return data
        except Exception:
            return None
    except Exception:
        return None

def _cli_kline(mint: str, resolution: str, from_ts: int, to_ts: int, chain: str = "sol") -> dict | None:
    data = _run_gmgn_cli(["market", "kline", "--chain", chain, "--address", mint, "--resolution", resolution, "--from", str(from_ts), "--to", str(to_ts)])
    if data is None:
        return None
    # data may be {"list": [...]} or {"data": {"list": [...]}} or list
    lst = None
    if isinstance(data, dict):
        lst = data.get("list") or data.get("data", {}).get("list") if isinstance(data.get("data"), dict) else None
        if lst is None and isinstance(data.get("list"), list):
            lst = data.get("list")
        if lst is None and isinstance(data, list):
            lst = data
    elif isinstance(data, list):
        lst = data
    if isinstance(lst, list) and lst:
        return {"list": lst}
    return None

def _cli_trending(chain: str, interval: str, limit: int) -> dict | None:
    data = _run_gmgn_cli(["market", "trending", "--chain", chain, "--interval", interval, "--limit", str(limit)])
    if data is None:
        return None
    # trending raw: {"code":0,"data":{"rank":[...]}} or {"list": [...]}
    if isinstance(data, dict):
        if "rank" in data:
            return {"list": data["rank"]}
        if isinstance(data.get("data"), dict) and "rank" in data["data"]:
            return {"list": data["data"]["rank"]}
        if "list" in data:
            return data
        if isinstance(data.get("data"), list):
            return {"list": data["data"]}
    return data if isinstance(data, dict) else None

def _cli_trenches(chain: str, limit: int) -> dict | None:
    data = _run_gmgn_cli(["market", "trenches", "--chain", chain, "--limit", str(limit)])
    if data is None:
        return None
    # trenches raw: {"code":0,"data":{"new_creation":[...],"near_completion":[...],"completed":[...]}} or similar
    if isinstance(data, dict):
        if "new_creation" in data or "completed" in data:
            return data
        if isinstance(data.get("data"), dict) and ("new_creation" in data["data"] or "completed" in data["data"]):
            return data["data"]
    return data if isinstance(data, dict) else None


def _envelope(data: Any, source: str = "gmgn") -> dict:
    return {"data": data, "source": source, "observed_at": int(time.time())}


# --- public API matching official contract ---

def get_kline(
    mint: str,
    interval: str = "1m",
    limit: int = 100,
    settings: Settings | None = None,
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
    chain: str = "sol",
) -> dict | None:
    """Official GET /v1/market/token_kline?chain&address&resolution&from&to (cli first, then http)."""
    try:
        _api_key(settings)
    except MissingGMGNKeyError:
        return None
    resolution = interval if interval in ("30s", "1m", "5m", "15m", "1h", "4h", "1d") else "1m"
    now = int(time.time())
    if from_ts is None or to_ts is None:
        sec_per = 30 if resolution == "30s" else 60 if resolution == "1m" else 300 if resolution == "5m" else 60
        to_ts = now
        from_ts = now - limit * sec_per
    # try gmgn-cli first (handles IPv4 + auth correctly)
    cli_data = _cli_kline(mint, resolution, from_ts, to_ts, chain=chain)
    if cli_data and isinstance(cli_data.get("list"), list) and cli_data["list"]:
        return _envelope({"kline": cli_data["list"], "interval": resolution, "from": from_ts, "to": to_ts}, "gmgn")
    # fallback to direct http (may fail on IPv6, but try)
    params: dict[str, Any] = {
        "chain": chain,
        "address": mint,
        "resolution": resolution,
        "from": from_ts,
        "to": to_ts,
    }
    payload = _fetch_json(KLINE_PATH, params, settings=settings, method="GET")
    if not payload:
        return None
    if payload.get("code") not in (0, None, "0"):
        if payload.get("code") != 0:
            return None
    data = payload.get("data") or payload
    lst = None
    if isinstance(data, dict):
        lst = data.get("list") or data.get("kline") or data.get("data")
        if lst is None and isinstance(data.get("data"), list):
            lst = data.get("data")
    elif isinstance(data, list):
        lst = data
    if isinstance(lst, list) and lst:
        return _envelope({"kline": lst, "interval": resolution, "from": from_ts, "to": to_ts}, "gmgn")
    if isinstance(payload.get("list"), list) and payload["list"]:
        return _envelope({"kline": payload["list"], "interval": resolution, "from": from_ts, "to": to_ts}, "gmgn")
    return None


def _extract_close(candle: Any) -> float | None:
    if isinstance(candle, dict):
        for k in ("close", "c", "close_price", "price"):
            if k in candle and candle[k] is not None:
                try:
                    return float(candle[k])
                except Exception:
                    continue
        if "c" in candle:
            try:
                return float(candle["c"])
            except Exception:
                pass
    elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
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


def _extract_time(candle: Any) -> int | None:
    """Return candle open time as unix seconds (official time is ms)."""
    if isinstance(candle, dict):
        t = candle.get("time") or candle.get("t") or candle.get("timestamp")
        if t is not None:
            try:
                v = int(t)
                # if ms (>1e12), convert to s
                if v > 1_000_000_000_0:
                    return v // 1000
                return v
            except Exception:
                pass
    elif isinstance(candle, (list, tuple)) and len(candle) >= 1:
        try:
            v = int(candle[0])
            if v > 1_000_000_000_0:
                return v // 1000
            return v
        except Exception:
            pass
    return None


def kline_to_gains(kline: list[Any], interval: str) -> dict | None:
    """Fallback when no signal_ts anchoring available: entry = first candle close."""
    if not kline or len(kline) < 2:
        return None
    entry = _extract_close(kline[0])
    if not entry or entry == 0:
        return None
    sec = 30 if interval == "30s" else 60
    targets = {"90": 90, "180": 180, "300": 300, "900": 900}
    out: dict[str, float] = {}
    for key, secs in targets.items():
        idx = max(0, round(secs / sec))
        if idx >= len(kline):
            continue
        close = _extract_close(kline[idx])
        if close is not None:
            out[f"gain_{key}_pct"] = (close - entry) / entry * 100
            out[f"price_{key}"] = close
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


def kline_gains_anchored(kline: list[Any], signal_ts: int, interval: str = "30s") -> dict | None:
    """Anchor to signal_ts via candle.time: entry is first candle >= signal_ts."""
    if not kline:
        return None
    # sort by time ascending
    def _t(c):
        return _extract_time(c) or 0

    sorted_k = sorted(kline, key=_t)
    # find entry index: first candle with time >= signal_ts
    entry_idx = None
    for i, c in enumerate(sorted_k):
        t = _extract_time(c)
        if t is not None and t >= signal_ts:
            entry_idx = i
            break
    if entry_idx is None:
        # fallback to first candle if all before signal (young mint)
        entry_idx = 0
    entry_candle = sorted_k[entry_idx]
    entry = _extract_close(entry_candle)
    if not entry or entry == 0:
        return None
    entry_time = _extract_time(entry_candle) or signal_ts
    out: dict[str, float] = {}
    # for each target offset, find candle whose time is closest to signal_ts+offset
    for key, off in (("90", 90), ("180", 180), ("300", 300), ("900", 900)):
        target = signal_ts + off
        best = None
        best_dt = None
        for c in sorted_k:
            t = _extract_time(c)
            if t is None:
                continue
            dt = abs(t - target)
            if best_dt is None or dt < best_dt:
                best_dt = dt
                best = c
        if best is not None:
            close = _extract_close(best)
            if close is not None:
                out[f"gain_{key}_pct"] = (close - entry) / entry * 100
                out[f"price_{key}"] = close
                out[f"time_{key}"] = float(_extract_time(best) or 0)
    # MFE/MAE over window signal_ts .. signal_ts+900 (or full kline if shorter)
    window_end = signal_ts + 900
    highs: list[float] = []
    lows: list[float] = []
    for c in sorted_k:
        t = _extract_time(c)
        if t is None or t < entry_time or t > window_end:
            continue
        h, lo = _extract_high_low(c)
        if h is not None:
            highs.append(h)
        if lo is not None:
            lows.append(lo)
    if highs:
        out["mfe_pct"] = (max(highs) - entry) / entry * 100
    if lows:
        out["mae_pct"] = (min(lows) - entry) / entry * 100
    out["entry_price"] = entry
    out["entry_time"] = float(entry_time)
    out["signal_ts"] = float(signal_ts)
    out["kline_interval"] = interval
    out["kline_len"] = len(sorted_k)
    return out


def get_kline_gains(mint: str, settings: Settings | None = None, signal_ts: int | None = None) -> dict | None:
    """Try 30s then 1m Kline, anchored if signal_ts given, otherwise fallback to first-candle anchoring."""
    for interval in ("30s", "1m"):
        # if anchored, request window around signal_ts; else recent window
        if signal_ts is not None:
            from_ts = signal_ts - 60
            to_ts = signal_ts + 1800
        else:
            from_ts = None
            to_ts = None
        env = get_kline(mint, interval=interval, limit=200, settings=settings, from_ts=from_ts, to_ts=to_ts)
        if not env:
            continue
        kline = None
        data = env.get("data") or {}
        if isinstance(data, dict):
            kline = data.get("kline") or data.get("list") or data.get("data")
            if not isinstance(kline, list):
                kline = data if isinstance(data, list) else None
        elif isinstance(data, list):
            kline = data
        if not kline:
            continue
        gains = kline_gains_anchored(kline, signal_ts, interval) if signal_ts is not None else kline_to_gains(kline, interval)
        if not gains:
            continue
        gains["source"] = "gmgn"
        gains["observed_at"] = env.get("observed_at", int(time.time()))
        gains["interval"] = interval
        return gains
    return None


def get_token(mint: str, settings: Settings | None = None) -> dict | None:
    # Token info via official search/detail? Use /v1/token/info if available
    payload = _fetch_json("/v1/token/info", {"chain": "sol", "address": mint}, settings=settings, method="GET")
    if payload is None:
        payload = _fetch_json("/v1/token/detail", {"chain": "sol", "address": mint}, settings=settings, method="GET")
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_pool(mint: str, settings: Settings | None = None) -> dict | None:
    payload = _fetch_json("/v1/token/pool", {"chain": "sol", "address": mint}, settings=settings, method="GET")
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_security(mint: str, settings: Settings | None = None) -> dict | None:
    payload = _fetch_json("/v1/token/security", {"chain": "sol", "address": mint}, settings=settings, method="GET")
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_holders_traders(mint: str, settings: Settings | None = None) -> dict | None:
    payload = _fetch_json("/v1/token/holders", {"chain": "sol", "address": mint, "limit": 50}, settings=settings, method="GET")
    if payload:
        return _envelope(payload.get("data") or payload, "gmgn")
    return None


def get_trending(chain: str = "sol", interval: str = "1h", limit: int = 20, settings: Settings | None = None, **kw) -> dict | None:
    cli = _cli_trending(chain, interval, limit)
    if cli and isinstance(cli.get("list"), list) and cli["list"]:
        return _envelope(cli.get("list") if isinstance(cli.get("list"), list) else cli, "gmgn")
    params: dict[str, Any] = {"chain": chain, "interval": interval, "limit": limit}
    params.update(kw)
    payload = _fetch_json(RANK_PATH, params, settings=settings, method="GET")
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def get_trenches(chain: str = "sol", limit: int = 20, settings: Settings | None = None) -> dict | None:
    cli = _cli_trenches(chain, limit)
    if cli:
        return _envelope(cli.get("data") or cli, "gmgn")
    body = {"chain": chain, "limit": limit}
    payload = _fetch_json(TRENCHES_PATH, {}, settings=settings, method="POST", json_body=body)
    if payload is None:
        return None
    return _envelope(payload.get("data") or payload, "gmgn")


def test_api_key(settings: Settings | None = None) -> tuple[bool, str]:
    try:
        _api_key(settings)
    except MissingGMGNKeyError as exc:
        return False, str(exc)
    try:
        payload = _fetch_json(KLINE_PATH, {"chain": "sol", "address": "So11111111111111111111111111111111112", "resolution": "1m", "from": int(time.time()) - 120, "to": int(time.time())}, settings=settings, method="GET")
    except MissingGMGNKeyError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"network error: {exc}"
    if not payload:
        return False, "GMGN empty or blocked (check IPv4 / rate limit)"
    if payload.get("code") not in (0, None, "0"):
        return False, f"GMGN error: {payload.get('message') or payload.get('reason') or payload}"
    return True, "GMGN API OK"


def fetch_json(path: str, params: dict[str, Any] | None = None, settings: Settings | None = None) -> dict | None:
    return _fetch_json(path, params or {}, settings=settings, method="GET")
