from __future__ import annotations

import json
import os
from pathlib import Path

from smartalpha.types import WalletConfig

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_wallets(path: Path | None = None) -> list[WalletConfig]:
    p = path or ROOT / "wallets.json"
    if not p.exists():
        p = ROOT / "wallets.example.json"
    data = json.loads(p.read_text())
    out: list[WalletConfig] = []
    for w in data.get("wallets", []):
        addr = w["address"]
        if addr.startswith("REPLACE_"):
            continue
        out.append(
            WalletConfig(
                address=addr,
                tier=w.get("tier", "accumulator"),
                weight=float(w.get("weight", 1.0)),
                label=w.get("label", ""),
            )
        )
    return out


class Settings:
    rpc_url: str = _env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    helius_key: str = _env("HELIUS_API_KEY", "")
    solscan_key: str = _env("SOLSCAN_API_KEY", "")
    gmgn_cookie: str = _env("GMGN_COOKIE", "")
    gmgn_cookie_file: str = _env("GMGN_COOKIE_FILE", "data/gmgn.cookie")
    telegram_token: str = _env("TELEGRAM_BOT_TOKEN", "")
    telegram_chat: str = _env("TELEGRAM_CHAT_ID", "")
    poll_interval: float = float(_env("POLL_INTERVAL_SEC", "15"))
    cluster_window: int = int(_env("CLUSTER_WINDOW_SEC", "300"))
    cluster_min_wallets: int = int(_env("CLUSTER_MIN_WALLETS", "3"))
    cluster_min_score: float = float(_env("CLUSTER_MIN_SCORE", "4.0"))
    dump_sell_ratio: float = float(_env("DUMP_SELL_RATIO", "0.6"))
    dump_min_sellers: int = int(_env("DUMP_MIN_SELLERS", "2"))
    backtest_slippage: float = float(_env("BACKTEST_SLIPPAGE_PCT", "15")) / 100
    backtest_delay: int = int(_env("BACKTEST_COPY_DELAY_SEC", "30"))
    db_path: Path = ROOT / "data" / "smartalpha.db"
    # launch pipeline (Helius watch-launches)
    launch_settle_sec: int = int(_env("LAUNCH_SETTLE_SEC", "90"))
    launch_pair_poll_sec: int = int(_env("LAUNCH_PAIR_POLL_SEC", "15"))
    launch_max_sigs: int = int(_env("LAUNCH_MAX_SIGS", "40"))
    launch_max_buyers: int = int(_env("LAUNCH_MAX_BUYERS", "12"))
    signal_min_hot_buyers: int = int(_env("SIGNAL_MIN_HOT_BUYERS", "2"))
    # Live: unknown liq fails. Historical OOS: stale low liq is not entry liq.
    signal_allow_unknown_liq: bool = _env("SIGNAL_ALLOW_UNKNOWN_LIQ", "0") in (
        "1",
        "true",
        "True",
        "yes",
    )
    backtest_ignore_stale_liq: bool = _env("BACKTEST_IGNORE_STALE_LIQ", "1") not in (
        "0",
        "false",
        "False",
        "no",
    )
    backtest_stale_liq_hours: float = float(_env("BACKTEST_STALE_LIQ_HOURS", "48"))
    backtest_tp_pct: float = float(_env("BACKTEST_TP_PCT", "100"))
    backtest_sl_pct: float = float(_env("BACKTEST_SL_PCT", "30"))
    backtest_hard_tp_pct: float = float(_env("BACKTEST_HARD_TP_PCT", "120"))
    backtest_trail_activate_pct: float = float(_env("BACKTEST_TRAIL_ACTIVATE_PCT", "50"))
    backtest_trail_drawdown_pct: float = float(_env("BACKTEST_TRAIL_DRAWDOWN_PCT", "30"))
    backtest_early_cut_h1_pct: float = float(_env("BACKTEST_EARLY_CUT_H1_PCT", "0"))
    backtest_stall_h6_pct: float = float(_env("BACKTEST_STALL_H6_PCT", "15"))
    backtest_scale_half_pct: float = float(_env("BACKTEST_SCALE_HALF_PCT", "100"))
    backtest_max_hold_min: int = int(_env("BACKTEST_MAX_HOLD_MIN", "30"))
    backtest_ladder_tp1_pct: float = float(_env("BACKTEST_LADDER_TP1_PCT", "100"))
    backtest_ladder_tp2_pct: float = float(_env("BACKTEST_LADDER_TP2_PCT", "200"))
    # 5k default: early pump pools often sit 3–8k; 10k was killing real signals
    signal_min_liquidity_usd: float = float(_env("SIGNAL_MIN_LIQUIDITY_USD", "5000"))
    session_min_gain_pct: float = float(_env("SESSION_MIN_GAIN_PCT", "300"))
    session_mint_limit: int = int(_env("SESSION_MINT_LIMIT", "15"))
    session_min_mint_hits: int = int(_env("SESSION_MIN_MINT_HITS", "2"))
    session_max_buyers: int = int(_env("SESSION_MAX_BUYERS", "15"))
    session_min_grade: str = _env("SESSION_MIN_GRADE", "medium")
    session_refresh_sec: int = int(_env("SESSION_REFRESH_SEC", "1800"))
    paper_snapshot_delays: tuple[int, ...] = tuple(
        int(x.strip())
        for x in _env("PAPER_SNAPSHOT_DELAYS_SEC", "0,90,180,300,900").split(",")
        if x.strip().isdigit()
    )
    paper_log_path: Path = ROOT / "data" / "paper_signals.csv"


def rpc_url(settings: Settings | None = None) -> str:
    settings = settings or Settings()
    if settings.helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={settings.helius_key}"
    return settings.rpc_url
