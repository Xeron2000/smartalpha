from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from smartalpha.config import Settings, rpc_url
from smartalpha.db import Store
from smartalpha.demo import DEMO_MINT, demo_events, run_self_check
from smartalpha.dump_detect import analyze_dump
from smartalpha.launch_intel import analyze_launch
from smartalpha.launch_watch import process_new_mint, watch_pump_launches
from smartalpha.paper_log import catch_up_paper_snapshots, export_paper_csv, paper_health
from smartalpha.providers.dexscreener import dex_token_outcome
from smartalpha.rpc import SolanaRpc
from smartalpha.signal_rules import classify_signal


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="smartalpha",
        description="Solana Launch Microstructure Alpha & Sniper Engine",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("self-check", help="Run offline sanity check")

    w = sub.add_parser("watch-launches", help="Helius WS → First-Principles Gate → Paper")
    w.add_argument("--once-mint", help="Debug: process single mint without websocket")

    pl = sub.add_parser("paper-log", help="Export / catch-up paper signal log")
    pl.add_argument(
        "action",
        choices=("export", "catch-up", "list", "health"),
        help="export CSV | catch-up delayed snapshots | list recent | health",
    )
    pl.add_argument("--out", default="data/paper_signals.csv", help="CSV path for export")

    pr = sub.add_parser("prove", help="Statistical OOS + Paper proof protocol → verdict")
    pr.add_argument("mints_file", nargs="?", default="data/auto_discover.json", help="Candidates file")
    pr.add_argument("--min-paper-strict", type=int, default=30)
    pr.add_argument("--limit", type=int, default=0)

    s = sub.add_parser("scan-mint", help="Analyze launch microstructure of a single mint")
    s.add_argument("mint", help="Token mint address")
    s.add_argument("--pair", help="Override DEX pair address")

    d = sub.add_parser("dump", help="Analyze dump risk for a mint")
    d.add_argument("mint", nargs="?", help="Token mint address")
    d.add_argument("--demo", action="store_true", help="Analyze synthetic demo mint")

    args = p.parse_args(argv)
    settings = Settings()

    if args.cmd == "self-check":
        ok = run_self_check()
        sys.exit(0 if ok else 1)

    elif args.cmd == "watch-launches":
        if args.once_mint:
            rpc = SolanaRpc(rpc_url(settings))
            store = Store(settings.db_path)
            sig = process_new_mint(args.once_mint, "", "cli-manual", settings=settings, rpc=rpc, store=store)
            print(json.dumps({"signal": bool(sig), "mint": args.once_mint}, indent=2))
            return
        asyncio.run(watch_pump_launches(settings))

    elif args.cmd == "paper-log":
        store = Store(settings.db_path)
        if args.action == "health":
            print(json.dumps(paper_health(settings=settings, store=store), indent=2))
        elif args.action == "catch-up":
            print(json.dumps(catch_up_paper_snapshots(settings=settings, store=store), indent=2))
        elif args.action == "list":
            rows = store.list_paper_signals(limit=20)
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        elif args.action == "export":
            n = export_paper_csv(Path(args.out), settings=settings, store=store)
            print(json.dumps({"exported": n, "path": args.out}))

    elif args.cmd == "prove":
        from smartalpha.prove import run_prove
        report = run_prove(
            Path(args.mints_file) if args.mints_file else None,
            settings=settings,
            min_paper_strict=args.min_paper_strict,
            limit=args.limit,
        )
        print(json.dumps(report, default=lambda o: getattr(o, "__dict__", str(o)), indent=2, ensure_ascii=False))

    elif args.cmd == "scan-mint":
        rpc = SolanaRpc(rpc_url(settings))
        intel = analyze_launch(args.mint, rpc, pair_address=args.pair, settings=settings)
        outcome = dex_token_outcome(args.mint)
        liq = outcome.get("liquidity_usd") if outcome else None
        level = classify_signal(
            intel,
            min_unique_buyers=settings.signal_min_unique_buyers,
            liquidity_usd=liq,
            min_liquidity_usd=settings.signal_min_liquidity_usd,
        )
        print(
            json.dumps(
                {
                    "mint": args.mint,
                    "level": level.value,
                    "strict_entry": level.value == "strong",
                    "liquidity_usd": liq,
                    "unique_buyers": len(set(b.wallet for b in intel.buyers)),
                    "copytrap_risk": intel.copytrap_risk,
                    "recommendation": intel.recommendation,
                    "notes": intel.notes,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    elif args.cmd == "dump":
        if args.demo:
            events = demo_events()
            mint = DEMO_MINT
        else:
            if not args.mint:
                print("mint required (or use --demo)", file=sys.stderr)
                sys.exit(1)
            store = Store(settings.db_path)
            events = store.events_since(int(time.time()) - 3600, mint=args.mint)
            mint = args.mint
        report = analyze_dump(events, mint, settings=settings)
        print(json.dumps(getattr(report, "__dict__", {}), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
