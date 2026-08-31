from __future__ import annotations

import os
from pathlib import Path

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


class Settings:
    rpc_url: str = _env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    helius_key: str = _env("HELIUS_API_KEY", "")
    solscan_key: str = _env("SOLSCAN_API_KEY", "")
    telegram_token: str = _env("TELEGRAM_BOT_TOKEN", "")
    telegram_chat: str = _env("TELEGRAM_CHAT_ID", "")
    dump_sell_ratio: float = float(_env("DUMP_SELL_RATIO", "0.6"))
    dump_min_sellers: int = int(_env("DUMP_MIN_SELLERS", "2"))
    db_path: Path = ROOT / "data" / "smartalpha.db"

    # Launch pipeline
    launch_settle_sec: int = int(_env("LAUNCH_SETTLE_SEC", "90"))
    launch_pair_poll_sec: int = int(_env("LAUNCH_PAIR_POLL_SEC", "15"))
    launch_max_sigs: int = int(_env("LAUNCH_MAX_SIGS", "40"))
    launch_max_buyers: int = int(_env("LAUNCH_MAX_BUYERS", "12"))
    signal_min_hot_buyers: int = int(_env("SIGNAL_MIN_HOT_BUYERS", "2"))
    signal_allow_unknown_liq: bool = _env("SIGNAL_ALLOW_UNKNOWN_LIQ", "0") in ("1", "true", "True", "yes")

    # Exit strategy parameters
    exit_mode: str = _env("EXIT_MODE", "fixed")  # fixed (+80% full TP) | scale (50% @ 2x + trail)
    backtest_tp_pct: float = float(_env("BACKTEST_TP_PCT", "80"))
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

    # First-Principles Strategy Invariants (STRATEGY_SPEC.md)
    signal_min_liquidity_usd: float = float(_env("SIGNAL_MIN_LIQUIDITY_USD", "3000"))
    signal_min_unique_buyers: int = int(_env("SIGNAL_MIN_UNIQUE_BUYERS", "8"))
    signal_min_buy_sell_ratio: float = float(_env("SIGNAL_MIN_BUY_SELL_RATIO", "1.5"))
    signal_min_velocity: float = float(_env("SIGNAL_MIN_VELOCITY", "0.5"))
    paper_trade_size_usd: float = float(_env("PAPER_TRADE_SIZE_USD", "100.0"))

    # Paper trading snapshots
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
