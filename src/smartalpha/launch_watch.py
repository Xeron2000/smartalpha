from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from smartalpha.config import Settings
from smartalpha.db import Store
from smartalpha.launch_intel import analyze_launch
from smartalpha.providers.dexscreener import dex_pair_address, dex_token_outcome
from smartalpha.pump import PUMP_PROGRAM, parse_pump_create_tx
from smartalpha.rpc import SolanaRpc
from smartalpha.signal_rules import classify_signal, should_follow_launch
from smartalpha.telegram import notify


@dataclass
class LaunchSignal:
    mint: str
    creator: str
    signature: str
    ts: int
    recommendation: str
    copytrap_risk: str
    bundler_wallets: list[str] = field(default_factory=list)
    top_buyers: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def process_new_mint(
    mint: str,
    creator: str,
    signature: str,
    *,
    settings: Settings | None = None,
    rpc: SolanaRpc | None = None,
    store: Store | None = None,
) -> LaunchSignal | None:
    """Evaluate launch microstructure against STRATEGY_SPEC.md four pillars."""
    s = settings or Settings()
    rpc = rpc or SolanaRpc(_rpc_url(s))
    store = store or Store(s.db_path)

    if not store.try_seen_mint(mint, signature, creator):
        return None

    notes: list[str] = []
    intel = None
    liq: float | None = None
    vol: float | None = None
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
        )
        notes.extend(intel.notes)

        try:
            outcome = dex_token_outcome(mint)
            if outcome:
                liq = outcome.get("liquidity_usd")
                vol = outcome.get("gain_m5_pct")
        except Exception:
            liq = None

        notes.append(
            f"exit plan: {s.exit_mode} (TP=+{s.backtest_tp_pct:.0f}% full exit | SL=-{s.backtest_sl_pct:.0f}%)"
        )
        if s.signal_min_liquidity_usd > 0:
            notes.append(
                f"liquidity_usd={liq if liq is not None else 'unknown'} "
                f"min={s.signal_min_liquidity_usd:.0f}"
            )

        top_buyers = [
            {"wallet": b.wallet, "buy_sol": b.buy_sol, "follow_score": b.follow_score}
            for b in intel.buyers[:5]
        ]

        payload = {
            "mint": mint,
            "creator": creator,
            "signature": signature,
            "recommendation": intel.recommendation,
            "copytrap_risk": intel.copytrap_risk,
            "unique_buyers": len(set(b.wallet for b in intel.buyers)),
            "bundler_wallets": intel.bundler_wallets,
            "top_buyers": top_buyers,
            "notes": notes,
        }
        store.save_alert("launch", mint, json.dumps(payload, ensure_ascii=False), int(time.time()))

        is_strict = should_follow_launch(
            intel,
            min_unique_buyers=s.signal_min_unique_buyers,
            liquidity_usd=liq,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            volume_usd=vol,
            min_velocity=s.signal_min_velocity,
            allow_unknown_liq=s.signal_allow_unknown_liq,
            ignore_stale_low_liq=False,
        )
        level = classify_signal(
            intel,
            min_unique_buyers=s.signal_min_unique_buyers,
            liquidity_usd=liq,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            volume_usd=vol,
            min_velocity=s.signal_min_velocity,
            allow_unknown_liq=s.signal_allow_unknown_liq,
            ignore_stale_low_liq=False,
        ).value

        # Telegram alert on STRICT / MEDIUM
        if s.telegram_token and s.telegram_chat and level in ("strong", "medium"):
            tag = "🎯 STRONG ENTRY" if is_strict else "👀 WATCH LAUNCH"
            msg = (
                f"{tag} [level={level}]\n"
                f"mint: `{mint}`\n"
                f"pair: `{pair or 'none'}`\n"
                f"liquidity: ${liq:,.0f} (min ${s.signal_min_liquidity_usd:,.0f})\n"
                f"unique buyers: {len(set(b.wallet for b in intel.buyers))}\n"
                f"copytrap: {intel.copytrap_risk}\n"
                f"notes: {'; '.join(notes[:4])}"
            )
            notify(s.telegram_token, s.telegram_chat, msg)

        signal = LaunchSignal(
            mint=mint,
            creator=creator,
            signature=signature,
            ts=int(time.time()),
            recommendation=intel.recommendation,
            copytrap_risk=intel.copytrap_risk,
            bundler_wallets=intel.bundler_wallets,
            top_buyers=top_buyers,
            notes=notes,
        )
    finally:
        from smartalpha.paper_log import PaperSignalInput, log_paper_signal

        inp = PaperSignalInput(
            mint=mint,
            signal_ts=int(time.time()),
            creator=creator,
            signature=signature,
            recommendation=intel.recommendation if intel else "skip",
            copytrap_risk=intel.copytrap_risk if intel else "unknown",
            intel=intel if intel is not None else object(),
            liquidity_usd=liq,
            notes=notes,
        )
        log_paper_signal(inp, settings=s, store=store)

    return signal


def _wait_for_pair(mint: str, wait_sec: int, poll_sec: int) -> str | None:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        pair = dex_pair_address(mint)
        if pair:
            return pair
        time.sleep(poll_sec)
    return dex_pair_address(mint)


def _rpc_url(s: Settings) -> str:
    if s.helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={s.helius_key}"
    return s.rpc_url


async def watch_pump_launches(settings: Settings | None = None) -> None:
    """Listen to live pump.fun token launches over Helius WebSocket."""
    s = settings or Settings()
    if not s.helius_key:
        raise SystemExit("HELIUS_API_KEY required for watch-launches (logsSubscribe)")

    ws_url = f"wss://mainnet.helius-rpc.com/?api-key={s.helius_key}"
    rpc = SolanaRpc(_rpc_url(s))
    store = Store(s.db_path)
    seen_sigs: set[str] = set()
    launches_processed = 0

    import websockets

    from smartalpha.paper_log import paper_health, schedule_paper_snapshots

    sub = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "confirmed"}],
    }

    print(
        f"  [First-Principles Sniper] settle={s.launch_settle_sec}s | "
        f"liq>={s.signal_min_liquidity_usd:.0f} | unique_buyers>={s.signal_min_unique_buyers}"
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
                    )
                    launches_processed += 1
                    if signal:
                        print(f"  → rec={signal.recommendation} trap={signal.copytrap_risk}")
                        asyncio.create_task(
                            schedule_paper_snapshots(
                                signal.mint, signal.ts, settings=s, store=store
                            )
                        )
        except Exception as exc:
            print(f"websocket error: {exc}; reconnecting in 5s")
            await asyncio.sleep(5)
