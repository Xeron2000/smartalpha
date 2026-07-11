from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from smartalpha.config import Settings
from smartalpha.db import Store
from smartalpha.exit_rules import exit_params
from smartalpha.funder import HotFunder, dex_pair_address, dex_token_outcome
from smartalpha.launch_intel import analyze_launch
from smartalpha.pump import PUMP_PROGRAM, parse_pump_create_tx
from smartalpha.rpc import SolanaRpc
from smartalpha.session_funders import refresh_session_hot_funders
from smartalpha.signal_rules import hot_organic_buyers, should_follow_launch
from smartalpha.telegram import notify
from smartalpha.trace_funders import trace_mint_funders


@dataclass
class LaunchSignal:
    mint: str
    creator: str
    signature: str
    ts: int
    recommendation: str
    copytrap_risk: str
    hot_funder_hits: list[str]
    bundler_wallets: list[str]
    top_funders: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def process_new_mint(
    mint: str,
    creator: str,
    signature: str,
    *,
    settings: Settings | None = None,
    rpc: SolanaRpc | None = None,
    store: Store | None = None,
    hot_funders: dict[str, HotFunder] | None = None,
) -> LaunchSignal | None:
    """Wait for early buys, trace funders, alert on hot funder hit."""
    s = settings or Settings()
    rpc = rpc or SolanaRpc(_rpc_url(s))
    store = store or Store(s.db_path)

    if not store.try_seen_mint(mint, signature, creator):
        return None

    t0 = time.time()
    notes: list[str] = []
    intel = None
    liq: float | None = None
    signal: LaunchSignal | None = None
    twait = 0.0

    try:
        pair = _wait_for_pair(mint, s.launch_settle_sec, s.launch_pair_poll_sec)
        twait = time.time() - t0
        if not pair:
            notes.append(f"no dex pair within {s.launch_settle_sec}s — using delayed retry")

        intel = analyze_launch(
            mint,
            rpc,
            pair_address=pair,
            max_sigs=s.launch_max_sigs,
            settings=s,
            hot_funders=hot_funders,
        )
        notes.extend(intel.notes)
        if hot_funders is not None:
            notes.append(f"session_hot_funders={len(hot_funders)}")

        try:
            trace = trace_mint_funders(
                mint, rpc, pair_address=pair, max_buyers=s.launch_max_buyers, settings=s
            )
            funders_ranked = [
                {"funder": h.funder, "buyers": h.count, "bundler": h.is_bundler_signal}
                for h in trace.funders[:5]
            ]
        except Exception as exc:
            notes.append(f"trace_err={type(exc).__name__}")
            funders_ranked = []

        ep = exit_params(s)
        try:
            outcome = dex_token_outcome(mint)
            liq = outcome.get("liquidity_usd") if outcome else None
        except Exception:
            liq = None
        notes.append(
            f"exit plan (default scale): half@{ep.scale_half_pct:.0f}% | "
            f"hybrid alt: early h1<{ep.early_cut_h1_pct:.0f}% stall h6<{ep.stall_h6_pct:.0f}% | "
            f"runner trail +{ep.trail_activate_pct:.0f}%/-{ep.trail_drawdown_pct:.0f}%"
        )
        if s.signal_min_liquidity_usd > 0:
            notes.append(
                f"liquidity_usd={liq if liq is not None else 'unknown'} "
                f"min={s.signal_min_liquidity_usd:.0f}"
            )

        payload = {
            "mint": mint,
            "creator": creator,
            "signature": signature,
            "recommendation": intel.recommendation,
            "copytrap_risk": intel.copytrap_risk,
            "hot_funder_hits": intel.hot_funder_hits,
            "hot_organic_buyers": len(hot_organic_buyers(intel)),
            "bundler_wallets": intel.bundler_wallets,
            "funders_ranked": funders_ranked,
            "notes": notes,
        }
        store.save_alert("launch", mint, json.dumps(payload, ensure_ascii=False), int(time.time()))

        signal = LaunchSignal(
            mint=mint,
            creator=creator,
            signature=signature,
            ts=int(time.time()),
            recommendation=intel.recommendation,
            copytrap_risk=intel.copytrap_risk,
            hot_funder_hits=intel.hot_funder_hits,
            bundler_wallets=intel.bundler_wallets,
            top_funders=funders_ranked,
            notes=notes,
        )

        if _should_alert(intel, s, liquidity_usd=liq):
            notify(f"🚀 LAUNCH SIGNAL\n{json.dumps(payload, ensure_ascii=False, indent=2)}", s)
    except Exception as exc:
        notes.append(f"process_err={type(exc).__name__}:{exc}")
        print(f"  process_new_mint ERROR mint={mint[:12]}... {exc}")
        import traceback
        traceback.print_exc()
    finally:
        # Paper row is mandatory for Phase2 — write even on partial failure
        from smartalpha.paper_log import PaperSignalInput, log_paper_signal

        if signal is None:
            signal = LaunchSignal(
                mint=mint,
                creator=creator,
                signature=signature,
                ts=int(time.time()),
                recommendation="skip",
                copytrap_risk="unknown",
                hot_funder_hits=[],
                bundler_wallets=[],
                top_funders=[],
                notes=notes or ["partial_failure"],
            )
        if intel is not None:
            try:
                log_paper_signal(
                    PaperSignalInput(
                        mint=mint,
                        signal_ts=signal.ts,
                        creator=creator,
                        signature=signature,
                        recommendation=intel.recommendation,
                        copytrap_risk=intel.copytrap_risk,
                        intel=intel,
                        liquidity_usd=liq,
                        notes=notes,
                    ),
                    settings=s,
                    store=store,
                )
                print(
                    f"  paper_log: mint={mint[:12]}... signal_ts={signal.ts} "
                    f"rec={intel.recommendation} hot={len(intel.hot_funder_hits)}"
                )
            except Exception as exc:
                print(f"  paper_log ERROR: {exc}")
                import traceback
                traceback.print_exc()
        else:
            print(f"  paper_log SKIP (no intel) mint={mint[:12]}...")

        store.mark_seen_mint_done(mint)
        elapsed = time.time() - t0
        print(f"  done: pair_wait={twait:.1f}s total={elapsed:.1f}s")

    return signal


def _should_alert(intel, s: Settings, *, liquidity_usd: float | None = None) -> bool:
    # Live: require known liq ≥ min (allow_unknown=False); do not ignore stale
    return should_follow_launch(
        intel,
        min_hot_buyers=s.signal_min_hot_buyers,
        liquidity_usd=liquidity_usd,
        min_liquidity_usd=s.signal_min_liquidity_usd,
        allow_unknown_liq=s.signal_allow_unknown_liq,
        ignore_stale_low_liq=False,
    )


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
    s = settings or Settings()
    if not s.helius_key:
        raise SystemExit("HELIUS_API_KEY required for watch-launches (logsSubscribe)")

    ws_url = f"wss://mainnet.helius-rpc.com/?api-key={s.helius_key}"
    rpc = SolanaRpc(_rpc_url(s))
    store = Store(s.db_path)
    seen_sigs: set[str] = set()
    last_sig_processed: str | None = None
    launches_processed = 0

    # Fast path: load funders from disk so WS connects immediately (no 30m rediscover)
    hot_funders, refresh_notes, report_path = refresh_session_hot_funders(
        s, prefer_cache=True, max_cache_age_sec=7 * 86400
    )
    if not hot_funders:
        print("  no cache funders — running live auto-discover (slow)...")
        hot_funders, refresh_notes, report_path = refresh_session_hot_funders(
            s, prefer_cache=False
        )
    next_refresh_ts = time.time() + max(60, s.session_refresh_sec)
    next_health_ts = time.time() + 120

    import websockets

    from smartalpha.paper_log import paper_health, schedule_paper_snapshots

    sub = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "confirmed"}],
    }

    print(
        f"  settle={s.launch_settle_sec}s  strict: >={s.signal_min_hot_buyers} hot organic "
        f"+ liq>={s.signal_min_liquidity_usd:.0f}"
    )
    ep = exit_params(s)
    print(
        f"  exit: scale half@{ep.scale_half_pct:.0f}% (default) | "
        f"hybrid alt: early h1<{ep.early_cut_h1_pct:.0f}% stall h6<{ep.stall_h6_pct:.0f}% | "
        f"runner trail +{ep.trail_activate_pct:.0f}%/-{ep.trail_drawdown_pct:.0f}%"
    )
    print(f"  session funders: {len(hot_funders)} loaded from {report_path}")
    for note in refresh_notes:
        print(f"    - {note}")
    print(f"  paper health: {paper_health(settings=s, store=store)}")

    while True:
        try:
            async with websockets.connect(
                ws_url, ping_interval=20, ping_timeout=20, close_timeout=5
            ) as ws:
                await ws.send(json.dumps(sub))
                print("websocket connected, subscribed to pump.fun logs")

                if last_sig_processed:
                    print(f"  [gap] last_processed_sig={last_sig_processed[:16]}...")
                else:
                    print("  [gap] first connection, no gap recovery needed")

                async for raw in ws:
                    now = time.time()
                    if now >= next_health_ts:
                        h = paper_health(settings=s, store=store)
                        print(
                            f"  [health] paper={h['paper_rows']} strict={h['strict_rows']} "
                            f"launches={launches_processed} funders={len(hot_funders)}"
                        )
                        next_health_ts = now + 300

                    if now >= next_refresh_ts:
                        # Prefer reloading disk (cheap). Full rediscover only if empty/stale.
                        hot_funders, refresh_notes, report_path = await asyncio.to_thread(
                            refresh_session_hot_funders,
                            s,
                            prefer_cache=True,
                            max_cache_age_sec=s.session_refresh_sec,
                        )
                        if not hot_funders:
                            hot_funders, refresh_notes, report_path = await asyncio.to_thread(
                                refresh_session_hot_funders, s, prefer_cache=False
                            )
                        next_refresh_ts = time.time() + max(60, s.session_refresh_sec)
                        print(
                            f"  session funders refreshed: {len(hot_funders)} from {report_path}"
                        )
                        for note in refresh_notes[:5]:
                            print(f"    - {note}")
                    msg = json.loads(raw)
                    if msg.get("id") == 1 and "result" in msg:
                        print(f"subscription id: {msg['result']}")
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
                    print(f"new launch mint={mint[:12]}... creator={creator[:12]}...")

                    signal = await asyncio.to_thread(
                        process_new_mint,
                        mint,
                        creator,
                        sig,
                        settings=s,
                        rpc=rpc,
                        store=store,
                        hot_funders=hot_funders,
                    )
                    launches_processed += 1
                    if signal:
                        print(
                            f"  → rec={signal.recommendation} "
                            f"hot={signal.hot_funder_hits} trap={signal.copytrap_risk}"
                        )
                        asyncio.create_task(
                            schedule_paper_snapshots(
                                signal.mint, signal.ts, settings=s, store=store
                            )
                        )
                    last_sig_processed = sig
        except Exception as exc:
            print(f"websocket error: {exc}; reconnect in 5s")
            await asyncio.sleep(5)
