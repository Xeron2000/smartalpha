from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from smartalpha.config import Settings, rpc_url
from smartalpha.db import Store
from smartalpha.execution import ExecutionEngine
from smartalpha.launch_intel import analyze_launch
from smartalpha.paper_log import (
    PaperSignalInput,
    log_paper_signal,
    paper_health,
    schedule_paper_snapshots,
)
from smartalpha.providers.dexscreener import get_pair_meta
from smartalpha.pump import PUMP_PROGRAM, parse_pump_create_tx
from smartalpha.rpc import SolanaRpc
from smartalpha.signal_rules import classify_signal
from smartalpha.telegram import notify


@dataclass
class LaunchSignal:
    mint: str
    creator: str
    signature: str
    ts: int
    recommendation: str
    copytrap_risk: str
    level: str = "skip"
    strict_entry: bool = False
    liquidity_usd: float | None = None
    volume_usd: float | None = None
    price_usd: float | None = None
    buy_count: int = 0
    sell_count: int = 0
    bundler_wallets: list[str] = field(default_factory=list)
    top_buyers: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def process_new_mint(
    mint: str,
    creator: str,
    signature: str,
    *,
    launch_ts: int | None = None,
    settings: Settings | None = None,
    rpc: SolanaRpc | None = None,
    store: Store | None = None,
) -> LaunchSignal | None:
    """Evaluate launch microstructure against STRATEGY_SPEC.md four pillars."""
    s = settings or Settings()
    rpc = rpc or SolanaRpc(rpc_url(s))
    store = store or Store(s.db_path)

    if launch_ts is None and signature:
        try:
            create_tx = rpc.get_transaction(signature)
            launch_ts = int((create_tx or {}).get("blockTime") or 0) or None
        except Exception:
            launch_ts = None

    if not store.try_seen_mint(mint, signature, creator):
        return None

    notes: list[str] = []
    intel = None
    liq: float | None = None
    vol: float | None = None
    price: float | None = None
    signal: LaunchSignal | None = None

    try:
        pair = _wait_for_pair(mint, s.launch_settle_sec, s.launch_pair_poll_sec)
        if not pair:
            notes.append(f"no dex pair within {s.launch_settle_sec}s")

        intel = analyze_launch(
            mint,
            rpc,
            pair_address=pair,
            max_sigs=s.launch_max_sigs,
            settings=s,
            launch_ts=launch_ts,
            as_of_ts=int(time.time()),
        )
        if launch_ts is None:
            intel.notes.append("missing launch timestamp — strict disabled")
        notes.extend(intel.notes)

        try:
            outcome = get_pair_meta(mint)
            if outcome:
                liq = outcome.get("liquidity_usd")
                vol = outcome.get("volume_m5_usd")
                price = outcome.get("price_usd")
        except Exception:
            liq = None
            vol = None
            price = None

        notes.append(
            f"exit plan: SL=-{s.execution_stop_loss_pct:.0f}% | "
            f"TP1=+{s.execution_tp1_pct:.0f}%/{s.execution_tp1_fraction:.0%} "
            f"TP2=+{s.execution_tp2_pct:.0f}%/{s.execution_tp2_fraction:.0%} | "
            f"max_hold={s.execution_max_hold_sec}s"
        )
        if s.signal_min_liquidity_usd > 0:
            notes.append(
                f"liquidity_usd={liq if liq is not None else 'unknown'} "
                f"min={s.signal_min_liquidity_usd:.0f}"
            )

        top_buyers = [
            {"wallet": b.wallet, "buy_sol": b.buy_sol, "wallet_age_hours": b.wallet_age_hours}
            for b in intel.buyers[:5]
        ]

        level = classify_signal(
            intel,
            min_unique_buyers=s.signal_min_unique_buyers,
            liquidity_usd=liq,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            volume_usd=vol,
            min_velocity=s.signal_min_velocity,
            min_buy_sell_ratio=s.signal_min_buy_sell_ratio,
            max_buyer_share=s.signal_max_buyer_share,
            allow_unknown_liq=s.signal_allow_unknown_liq,
            allow_unknown_velocity=s.signal_allow_unknown_velocity,
            ignore_stale_low_liq=False,
        ).value
        is_strict = level == "strong"

        # Telegram alert on STRICT / MEDIUM
        if s.telegram_token and s.telegram_chat and level in ("strong", "medium"):
            tag = "🎯 STRONG ENTRY" if is_strict else "👀 WATCH LAUNCH"
            liq_text = f"${liq:,.0f}" if liq is not None else "unknown"
            msg = (
                f"{tag} [level={level}]\n"
                f"mint: `{mint}`\n"
                f"pair: `{pair or 'none'}`\n"
                f"liquidity: {liq_text} (min ${s.signal_min_liquidity_usd:,.0f})\n"
                f"unique buyers: {len(set(b.wallet for b in intel.buyers))}\n"
                f"copytrap: {intel.copytrap_risk}\n"
                f"notes: {'; '.join(notes[:4])}"
            )
            notify(s.telegram_token, s.telegram_chat, msg)

        signal_ts = int(time.time())
        signal = LaunchSignal(
            mint=mint,
            creator=creator,
            signature=signature,
            ts=signal_ts,
            recommendation=intel.recommendation,
            copytrap_risk=intel.copytrap_risk,
            level=level,
            strict_entry=is_strict,
            liquidity_usd=liq,
            volume_usd=vol,
            price_usd=price,
            buy_count=intel.buy_count,
            sell_count=intel.sell_count,
            bundler_wallets=intel.bundler_wallets,
            top_buyers=top_buyers,
            notes=notes,
        )
    finally:
        inp = PaperSignalInput(
            mint=mint,
            signal_ts=signal.ts if signal else int(time.time()),
            creator=creator,
            signature=signature,
            recommendation=intel.recommendation if intel else "skip",
            copytrap_risk=intel.copytrap_risk if intel else "unknown",
            intel=intel if intel is not None else object(),
            liquidity_usd=liq,
            notes=notes,
            buy_count=intel.buy_count if intel else 0,
            sell_count=intel.sell_count if intel else 0,
            top_buyer_share=intel.top_buyer_share if intel else 0.0,
            volume_usd=vol,
        )
        log_paper_signal(inp, settings=s, store=store)
        store.mark_seen_mint_done(mint)

    return signal


def _wait_for_pair(mint: str, wait_sec: int, poll_sec: int) -> str | None:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        pair = (get_pair_meta(mint) or {}).get("pair_address")
        if pair:
            return pair
        time.sleep(poll_sec)
    return (get_pair_meta(mint) or {}).get("pair_address")


async def watch_pump_launches(settings: Settings | None = None) -> None:
    """Listen to live pump.fun token launches over Helius WebSocket."""
    s = settings or Settings()
    if not s.helius_key:
        raise SystemExit("HELIUS_API_KEY required for watch-launches (logsSubscribe)")

    ws_url = f"wss://mainnet.helius-rpc.com/?api-key={s.helius_key}"
    rpc = SolanaRpc(rpc_url(s))
    store = Store(s.db_path)
    execution = ExecutionEngine(settings=s, store=store)
    seen_sigs: set[str] = set()

    import websockets

    await asyncio.to_thread(execution.reconcile_pending)
    for position in store.list_open_positions():
        asyncio.create_task(execution.monitor_position(position["mint"]))

    sub = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "confirmed"}],
    }

    print(
        f"  [First-Principles Execution] mode={s.execution_mode} | "
        f"settle={s.launch_settle_sec}s | liq>={s.signal_min_liquidity_usd:.0f} | "
        f"unique_buyers>={s.signal_min_unique_buyers}"
    )
    print(f"  paper health: {paper_health(settings=s, store=store)}")

    while True:
        try:
            async with websockets.connect(
                ws_url, ping_interval=20, ping_timeout=20, close_timeout=5
            ) as ws:
                await ws.send(json.dumps(sub))
                print("websocket connected, subscribed to pump.fun create events")

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("id") == 1 and "result" in msg:
                        continue
                    val = (msg.get("params") or {}).get("result", {}).get("value")
                    if not val:
                        continue
                    sig = val.get("signature")
                    logs = val.get("logs") or []
                    if not sig or sig in seen_sigs:
                        continue
                    if not any("Create" in ln for ln in logs):
                        continue
                    seen_sigs.add(sig)
                    if len(seen_sigs) > 5000:
                        seen_sigs.clear()

                    tx = await asyncio.to_thread(rpc.get_transaction, sig)
                    parsed = parse_pump_create_tx(tx)
                    if not parsed:
                        continue
                    mint, creator = parsed
                    print(f"new launch: mint={mint[:12]}... creator={creator[:12]}...")

                    signal = await asyncio.to_thread(
                        process_new_mint,
                        mint,
                        creator,
                        sig,
                        settings=s,
                        rpc=rpc,
                        store=store,
                        launch_ts=int(tx.get("blockTime") or time.time()),
                    )
                    if signal:
                        print(
                            f"  → level={signal.level} strict={signal.strict_entry} "
                            f"rec={signal.recommendation} trap={signal.copytrap_risk}"
                        )
                        if signal.strict_entry:
                            result = await asyncio.to_thread(execution.submit_entry, signal)
                            print(f"  → execution={result.status} {result.reason}".rstrip())
                            if result.position_opened:
                                asyncio.create_task(execution.monitor_position(signal.mint))
                        asyncio.create_task(
                            schedule_paper_snapshots(
                                signal.mint,
                                signal.ts,
                                settings=s,
                                store=store,
                                snapshot_provider=(
                                    execution.paper_snapshot_provider(signal)
                                    if signal.strict_entry
                                    else None
                                ),
                            )
                        )
        except Exception as exc:
            print(f"websocket error: {exc}; reconnecting in 5s")
            await asyncio.sleep(5)
