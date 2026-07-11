from __future__ import annotations

import re
import subprocess
from pathlib import Path

from smartalpha.config import ROOT, Settings

# Long-lived GMGN session keys; __cf_bm is refreshed automatically.
GMGN_CORE_KEYS = ("sid", "_wt", "_csrf", "_did")
CF_BM_KEY = "__cf_bm"


def parse_cookie(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def serialize_cookiejar(jar: dict[str, str], *, prefer_keys: tuple[str, ...] | None = None) -> str:
    keys = prefer_keys or tuple(jar.keys())
    parts: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if k in jar and k not in seen:
            parts.append(f"{k}={jar[k]}")
            seen.add(k)
    for k, v in jar.items():
        if k not in seen:
            parts.append(f"{k}={v}")
    return "; ".join(parts)


def cookie_file_path(settings: Settings | None = None) -> Path:
    settings = settings or Settings()
    p = Path(settings.gmgn_cookie_file)
    if not p.is_absolute():
        p = ROOT / p
    return p


def load_cookie(settings: Settings | None = None) -> str:
    settings = settings or Settings()
    if settings.gmgn_cookie:
        return settings.gmgn_cookie.strip()
    path = cookie_file_path(settings)
    if path.exists():
        return path.read_text().strip()
    return ""


def save_cookie(raw: str, settings: Settings | None = None) -> Path:
    path = cookie_file_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw.strip() + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def import_cookie(raw: str, settings: Settings | None = None) -> str:
    """Keep long-lived keys; strip noisy third-party cookies."""
    jar = parse_cookie(raw)
    core = {k: jar[k] for k in GMGN_CORE_KEYS if k in jar}
    if CF_BM_KEY in jar:
        core[CF_BM_KEY] = jar[CF_BM_KEY]
    if "sid" not in core:
        raise ValueError("missing sid — log in to gmgn.ai and copy cookies again")
    normalized = serialize_cookiejar(core, prefer_keys=(*GMGN_CORE_KEYS, CF_BM_KEY))
    save_cookie(normalized, settings)
    return normalized


def refresh_cf_bm(cookie: str) -> str:
    """Hit gmgn.ai homepage; Cloudflare returns fresh __cf_bm Set-Cookie."""
    jar = parse_cookie(cookie)
    core = serialize_cookiejar(jar, prefer_keys=(*GMGN_CORE_KEYS, CF_BM_KEY))
    cmd = [
        "curl",
        "-sS",
        "-m",
        "20",
        "-D",
        "-",
        "-o",
        "/dev/null",
        "-H",
        "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "-H",
        f"Cookie: {core}",
        "https://gmgn.ai/",
    ]
    try:
        headers = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return cookie

    for line in headers.splitlines():
        if not line.lower().startswith("set-cookie:"):
            continue
        m = re.search(r"__cf_bm=([^;]+)", line, re.I)
        if m:
            jar[CF_BM_KEY] = m.group(1)
            break
    return serialize_cookiejar(jar, prefer_keys=(*GMGN_CORE_KEYS, CF_BM_KEY))


def ensure_cookie(settings: Settings | None = None, *, persist: bool = True) -> str:
    raw = load_cookie(settings)
    if not raw:
        return ""
    refreshed = refresh_cf_bm(raw)
    if persist and refreshed != raw:
        save_cookie(refreshed, settings)
    return refreshed


def test_cookie(cookie: str) -> tuple[bool, str]:
    from smartalpha.mint_sources import GMGN_RANK, _gmgn_fetch_json

    if not cookie:
        return False, "no cookie — run: smartalpha gmgn-cookie import '<paste>'"
    payload = _gmgn_fetch_json(
        f"{GMGN_RANK}?orderby=priceChange&direction=desc&limit=1",
        cookie,
    )
    if not payload:
        return False, "GMGN blocked or network error"
    if payload.get("code") not in (0, None):
        return False, f"GMGN error: {payload.get('message') or payload.get('reason')}"
    rows = (payload.get("data") or {}).get("rank") or []
    if not rows:
        return False, "empty rank response"
    return True, f"ok — sample {rows[0].get('symbol')} +{rows[0].get('price_change_percent')}%"
