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
    telegram_token: str = _env("TELEGRAM_BOT_TOKEN", "")
    telegram_chat: str = _env("TELEGRAM_CHAT_ID", "")
    db_path: Path = ROOT / "data" / "smartalpha.db"

    # Launch pipeline
    launch_settle_sec: int = int(_env("LAUNCH_SETTLE_SEC", "90"))
    launch_pair_poll_sec: int = int(_env("LAUNCH_PAIR_POLL_SEC", "15"))
    launch_max_sigs: int = int(_env("LAUNCH_MAX_SIGS", "40"))
    signal_allow_unknown_liq: bool = _env("SIGNAL_ALLOW_UNKNOWN_LIQ", "0") in ("1", "true", "True", "yes")

    # First-Principles Strategy Invariants (STRATEGY_SPEC.md)
    signal_min_liquidity_usd: float = float(_env("SIGNAL_MIN_LIQUIDITY_USD", "3000"))
    signal_min_unique_buyers: int = int(_env("SIGNAL_MIN_UNIQUE_BUYERS", "8"))
    signal_min_buy_sell_ratio: float = float(_env("SIGNAL_MIN_BUY_SELL_RATIO", "1.5"))
    signal_max_buyer_share: float = float(_env("SIGNAL_MAX_BUYER_SHARE", "0.15"))
    signal_min_velocity: float = float(_env("SIGNAL_MIN_VELOCITY", "0.5"))
    signal_allow_unknown_velocity: bool = _env("SIGNAL_ALLOW_UNKNOWN_VELOCITY", "0") in ("1", "true", "True", "yes")
    paper_trade_size_usd: float = float(_env("PAPER_TRADE_SIZE_USD", "100.0"))

    # Execution is fail-closed. Canary requires an explicitly armed external signer.
    execution_mode: str = _env("EXECUTION_MODE", "paper")
    execution_signer_url: str = _env("EXECUTION_SIGNER_URL", "")
    execution_signer_token: str = _env("EXECUTION_SIGNER_TOKEN", "")
    execution_canary_armed: bool = _env("EXECUTION_CANARY_ARMED", "0") in ("1", "true", "True", "yes")
    execution_require_proven: bool = _env("EXECUTION_REQUIRE_PROVEN", "1") in ("1", "true", "True", "yes")
    execution_proof_path: Path = Path(
        _env("EXECUTION_PROOF_PATH", str(ROOT / "data" / "prove_report.json"))
    )
    execution_trade_size_sol: float = float(_env("EXECUTION_TRADE_SIZE_SOL", "0.05"))
    execution_max_open_positions: int = int(_env("EXECUTION_MAX_OPEN_POSITIONS", "1"))
    execution_max_daily_loss_sol: float = float(_env("EXECUTION_MAX_DAILY_LOSS_SOL", "0.10"))
    execution_max_slippage_pct: float = float(_env("EXECUTION_MAX_SLIPPAGE_PCT", "5"))
    execution_quote_ttl_sec: int = int(_env("EXECUTION_QUOTE_TTL_SEC", "5"))
    execution_poll_sec: int = int(_env("EXECUTION_POLL_SEC", "5"))
    execution_stop_loss_pct: float = float(_env("EXECUTION_STOP_LOSS_PCT", "20"))
    execution_tp1_pct: float = float(_env("EXECUTION_TP1_PCT", "50"))
    execution_tp2_pct: float = float(_env("EXECUTION_TP2_PCT", "100"))
    execution_tp1_fraction: float = float(_env("EXECUTION_TP1_FRACTION", "0.5"))
    execution_tp2_fraction: float = float(_env("EXECUTION_TP2_FRACTION", "0.3"))
    execution_trail_activate_pct: float = float(_env("EXECUTION_TRAIL_ACTIVATE_PCT", "50"))
    execution_trail_drawdown_pct: float = float(_env("EXECUTION_TRAIL_DRAWDOWN_PCT", "30"))
    execution_max_hold_sec: int = int(_env("EXECUTION_MAX_HOLD_SEC", "3600"))

    # Paper trading snapshots
    paper_snapshot_delays: tuple[int, ...] = tuple(
        int(x.strip())
        for x in _env("PAPER_SNAPSHOT_DELAYS_SEC", "0,90,180,300,900").split(",")
        if x.strip().isdigit()
    )


def rpc_url(settings: Settings | None = None) -> str:
    settings = settings or Settings()
    if settings.helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={settings.helius_key}"
    return settings.rpc_url
