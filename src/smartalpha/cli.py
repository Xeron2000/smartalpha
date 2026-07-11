from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from smartalpha.backtest import run_backtest
from smartalpha.cluster import ClusterEngine
from smartalpha.config import Settings, rpc_url
from smartalpha.db import Store
from smartalpha.demo import run_self_check
from smartalpha.dump_detect import analyze_dump
from smartalpha.launch_intel import analyze_launch
from smartalpha.monitor import Monitor
from smartalpha.rpc import SolanaRpc
from smartalpha.session_funders import refresh_session_hot_funders


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="smartalpha", description="Smart money parasite toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("self-check", help="Run offline demo self-check")

    m = sub.add_parser("monitor", help="Poll watched wallets → cluster/dump alerts")
    m.add_argument("--once", action="store_true", help="Single poll cycle")

    d = sub.add_parser("dump", help="Analyze dump risk for a mint")
    d.add_argument("mint", nargs="?", help="Token mint address")
    d.add_argument("--hours", type=int, default=1, help="Lookback window")
    d.add_argument("--demo", action="store_true", help="Analyze synthetic demo mint")

    b = sub.add_parser("backtest", help="Paper-copy backtest for watched wallets")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--demo", action="store_true", help="Use synthetic demo events")

    bf = sub.add_parser(
        "backtest-funders",
        help="Backtest hot-funder launch signals on historical pumped mints",
    )
    bf.add_argument(
        "mints_file",
        nargs="?",
        default="data/auto_discover.json",
        help="mints txt or auto_discover.json with recommended_funders (default)",
    )
    bf.add_argument("--limit", type=int, default=0, help="Max mints (0=all)")
    bf.add_argument("--position-sol", type=float, default=0.5, help="Paper size per trade")
    bf.add_argument("--tp", type=float, default=None, help="Take profit %% (default: BACKTEST_TP_PCT)")
    bf.add_argument("--sl", type=float, default=None, help="Stop loss %% (default: BACKTEST_SL_PCT)")
    bf.add_argument("--legacy", action="store_true", help="Use old loose entry rules")
    bf.add_argument(
        "--balanced",
        action="store_true",
        help="Balanced entry: STRONG or MEDIUM (any hot organic)",
    )
    bf.add_argument(
        "--exit",
        choices=("fixed", "dynamic", "scale", "hybrid", "ladder", "compare"),
        default="scale",
        help="Exit mode, or compare all on one pass",
    )

    wf = sub.add_parser(
        "walk-forward",
        help="Train funders on older mints, test signals on newer window",
    )
    wf.add_argument(
        "mints_file",
        nargs="?",
        default="data/auto_discover.json",
        help="mints txt or auto_discover.json",
    )
    wf.add_argument("--train-days", type=int, default=7, help="calendar split only")
    wf.add_argument("--test-days", type=int, default=7, help="calendar split only")
    wf.add_argument(
        "--split",
        choices=("chronological", "calendar"),
        default="chronological",
        help="chronological=older→train (default); calendar=rolling days from now",
    )
    wf.add_argument(
        "--train-ratio",
        type=float,
        default=0.7,
        help="chronological split: fraction of mints for train",
    )
    wf.add_argument("--min-mints", type=int, default=2, help="Min cross-mint hits for funder")
    wf.add_argument("--limit", type=int, default=0)
    wf.add_argument("--position-sol", type=float, default=0.5)

    pl = sub.add_parser("paper-log", help="Export / catch-up paper trading signal log")
    pl.add_argument(
        "action",
        choices=("export", "catch-up", "list", "health"),
        help="export CSV | catch-up delayed snapshots | list recent | health",
    )
    pl.add_argument("--out", default="data/paper_signals.csv", help="CSV path for export")

    pr = sub.add_parser(
        "prove",
        help="Prove/falsify strategy: walk-forward OOS + paper gates → verdict",
    )
    pr.add_argument(
        "mints_file",
        nargs="?",
        default="data/auto_discover.json",
        help="auto_discover json or mints file (default: data/auto_discover.json)",
    )
    pr.add_argument("--train-ratio", type=float, default=0.7)
    pr.add_argument(
        "--min-grade",
        default="medium",
        choices=("watch", "medium", "strong"),
        help="Min funder grade for OOS injection",
    )
    pr.add_argument("--position-sol", type=float, default=0.5)
    pr.add_argument("--min-oos-signals", type=int, default=10)
    pr.add_argument("--min-paper-strict", type=int, default=30)
    pr.add_argument("--limit", type=int, default=0, help="Max mints (0=all)")

    sub.add_parser("demo-cluster", help="Print demo cluster alert JSON")

    s = sub.add_parser("scan-mint", help="Behavior-first: score early buyers by funder/age/bundle")
    s.add_argument("mint", help="Token mint address")
    s.add_argument("--pair", help="Override DEX pair address")

    t = sub.add_parser("trace-funders", help="Early buyers → first funder → rank hot funders")
    t.add_argument("mint", help="Token mint that pumped (10x candidate)")
    t.add_argument("--pair", help="Override DEX pair address")
    t.add_argument("--max-buyers", type=int, default=15)

    w = sub.add_parser("watch-launches", help="Helius WS → auto trace-funders + alert")
    w.add_argument("--once-mint", help="Debug: process one mint without websocket")

    df = sub.add_parser(
        "discover-funders",
        help="Batch mints → aggregate repeat funders → discovery report",
    )
    df.add_argument(
        "mints_file",
        nargs="?",
        default="mints.txt",
        help="File with one mint per line (default: mints.txt)",
    )
    df.add_argument("--min-mints", type=int, default=2, help="Min tokens a funder must appear on")
    df.add_argument("--max-buyers", type=int, default=15)

    ad = sub.add_parser(
        "auto-discover",
        help="Auto find pump 10x candidates → trace funders → report",
    )
    ad.add_argument(
        "--min-gain",
        type=float,
        default=300.0,
        help="Min 24h %% gain filter (300 = 3x, 900 = 10x)",
    )
    ad.add_argument("--limit", type=int, default=15, help="Max mints to trace")
    ad.add_argument("--min-mints", type=int, default=2, help="Min tokens per funder")
    ad.add_argument("--max-buyers", type=int, default=15)
    ad.add_argument(
        "--mints-file",
        default="mints.txt",
        help="Optional extra mints file to merge",
    )
    ad.add_argument(
        "--dry-run",
        action="store_true",
        help="Only discover candidate mints (no RPC funder trace)",
    )

    gc = sub.add_parser("gmgn-cookie", help="Import / refresh / test GMGN cookies")
    gc_sub = gc.add_subparsers(dest="gmgn_cmd", required=True)
    gc_imp = gc_sub.add_parser("import", help="Paste browser cookie → data/gmgn.cookie")
    gc_imp.add_argument("cookie", nargs="?", help="Cookie string (or pipe via stdin)")
    gc_sub.add_parser("refresh", help="Refresh __cf_bm and save")
    gc_sub.add_parser("test", help="Verify GMGN API access")

    args = p.parse_args(argv)
    settings = Settings()

    if args.cmd == "self-check":
        ok = run_self_check()
        sys.exit(0 if ok else 1)

    if args.cmd == "monitor":
        mon = Monitor(settings)
        if args.once:
            print(mon.poll_once())
        else:
            mon.run_forever()

    elif args.cmd == "dump":
        if args.demo:
            from smartalpha.demo import DEMO_MINT, demo_events

            events = demo_events()
            mint = DEMO_MINT
        else:
            if not args.mint:
                print("mint required (or use --demo)", file=sys.stderr)
                sys.exit(1)
            store = Store(settings.db_path)
            since = int(time.time()) - args.hours * 3600
            events = store.events_since(since, mint=args.mint)
            mint = args.mint
        if not events:
            print(json.dumps({"error": "no events in DB for mint/window — run monitor first"}, indent=2))
            sys.exit(1)
        report = analyze_dump(events, mint, settings=settings)
        print(json.dumps(_report_dict(report), indent=2, ensure_ascii=False))

    elif args.cmd == "backtest":
        if args.demo:
            from smartalpha.backtest import run_demo_backtest

            res = run_demo_backtest(demo_events())
        else:
            res = run_backtest(
                days=args.days,
                settings=settings,
                rpc=SolanaRpc(rpc_url(settings)),
            )
        print(json.dumps(_backtest_dict(res), indent=2, ensure_ascii=False))

    elif args.cmd == "backtest-funders":
        from smartalpha.backtest_funders import (
            load_mints_with_pairs,
            run_exit_compare,
            run_funder_backtest,
            write_exit_compare_report,
            write_funder_backtest_report,
        )
        from smartalpha.config import ROOT

        path = Path(args.mints_file)
        if not path.is_absolute():
            path = ROOT / path
        mints = load_mints_with_pairs(path)
        if args.limit > 0:
            mints = mints[: args.limit]
        if not mints:
            print(json.dumps({"error": f"no mints in {path}"}, indent=2))
            sys.exit(1)
        if args.exit == "compare":
            cmp = run_exit_compare(
                mints,
                settings=settings,
                position_sol=args.position_sol,
                tp_pct=args.tp,
                sl_pct=args.sl,
                balanced=args.balanced,
                legacy=args.legacy,
                mints_source=path,
            )
            out_path = write_exit_compare_report(cmp)
            ranked = sorted(
                cmp.modes.items(),
                key=lambda kv: float(kv[1]["net_tpsl_sol"]),
                reverse=True,
            )
            print(
                json.dumps(
                    {
                        "report_path": str(out_path),
                        "mints_scanned": cmp.mints_scanned,
                        "signals": cmp.signals,
                        "liquidity_filtered": cmp.liquidity_filtered,
                        "ranked": [
                            {"mode": m, **{k: v for k, v in stats.items()}}
                            for m, stats in ranked
                        ],
                        "notes": cmp.notes,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        result = run_funder_backtest(
            mints,
            settings=settings,
            position_sol=args.position_sol,
            tp_pct=args.tp,
            sl_pct=args.sl,
            exit_mode=args.exit,
            balanced=args.balanced,
            legacy=args.legacy,
            mints_source=path,
        )
        out_path = write_funder_backtest_report(result)
        closed_h24 = result.wins_h24 + result.losses_h24
        closed_tpsl = result.wins_tpsl + result.losses_tpsl
        print(
            json.dumps(
                {
                    "report_path": str(out_path),
                    "mints_scanned": result.mints_scanned,
                    "signals_strict": result.signals,
                    "signals_legacy": result.signals_legacy,
                    "tp_pct": result.tp_pct,
                    "sl_pct": result.sl_pct,
                    "exit_mode": result.exit_mode,
                    "net_h24_sol": round(result.net_h24, 4),
                    "net_tpsl_sol": round(result.net_tpsl, 4),
                    "net_h6_sol": round(result.net_h6, 4),
                    "net_h1_sol": round(result.net_h1, 4),
                    "wins_h24": result.wins_h24,
                    "losses_h24": result.losses_h24,
                    "win_rate_h24": round(result.wins_h24 / closed_h24, 3)
                    if closed_h24
                    else None,
                    "wins_tpsl": result.wins_tpsl,
                    "losses_tpsl": result.losses_tpsl,
                    "win_rate_tpsl": round(result.wins_tpsl / closed_tpsl, 3)
                    if closed_tpsl
                    else None,
                    "notes": result.notes,
                    "signaled_trades": [
                        {
                            "mint": t.mint[:12] + "...",
                            "hot_organic_buyers": t.hot_organic_buyers,
                            "hot_funders": [f[:8] for f in t.hot_funders],
                            "gain_h24_pct": t.gain_h24_pct,
                            "pnl_h24_sol": round(t.pnl_h24_sol, 4)
                            if t.pnl_h24_sol is not None
                            else None,
                            "pnl_tpsl_sol": round(t.pnl_tpsl_sol, 4)
                            if t.pnl_tpsl_sol is not None
                            else None,
                            "tpsl_exit": t.tpsl_exit,
                        }
                        for t in result.trades
                        if t.signaled
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    elif args.cmd == "walk-forward":
        from smartalpha.backtest_funders import load_mints_with_pairs
        from smartalpha.config import ROOT
        from smartalpha.walk_forward import run_walk_forward, write_walk_forward_report

        path = Path(args.mints_file)
        if not path.is_absolute():
            path = ROOT / path
        mints = load_mints_with_pairs(path)
        if args.limit > 0:
            mints = mints[: args.limit]
        result = run_walk_forward(
            mints,
            settings=settings,
            split_mode=args.split,
            train_ratio=args.train_ratio,
            train_days=args.train_days,
            test_days=args.test_days,
            min_mint_hits=args.min_mints,
            position_sol=args.position_sol,
            mints_source=path,
        )
        out_path = write_walk_forward_report(result)
        print(
            json.dumps(
                {
                    "report_path": str(out_path),
                    "split_mode": result.split_mode,
                    "train_mints": len(result.train_mints),
                    "test_mints": len(result.test_mints),
                    "train_funders": len(result.train_funders),
                    "test_compare": result.test_compare,
                    "notes": result.notes,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    elif args.cmd == "paper-log":
        from smartalpha.config import ROOT
        from smartalpha.paper_log import (
            catch_up_paper_snapshots,
            export_paper_csv,
            paper_health,
        )

        if args.action == "catch-up":
            out = catch_up_paper_snapshots(settings=settings)
            print(json.dumps(out, indent=2))
        elif args.action == "list":
            store = Store(settings.db_path)
            rows = store.list_paper_signals(limit=20)
            print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        elif args.action == "health":
            print(json.dumps(paper_health(settings=settings), indent=2))
        else:
            out = Path(args.out)
            if not out.is_absolute():
                out = ROOT / out
            n = export_paper_csv(out, settings=settings)
            print(json.dumps({"csv_path": str(out), "rows": n}, indent=2))

    elif args.cmd == "prove":
        from smartalpha.config import ROOT
        from smartalpha.prove import prove_summary_text, run_prove

        path = Path(args.mints_file)
        if not path.is_absolute():
            path = ROOT / path
        report = run_prove(
            path,
            settings=settings,
            train_ratio=args.train_ratio,
            min_grade=args.min_grade,
            position_sol=args.position_sol,
            min_oos_signals=args.min_oos_signals,
            min_paper_strict=args.min_paper_strict,
            limit=args.limit,
        )
        print(prove_summary_text(report))
        print(
            json.dumps(
                {
                    "verdict": report.verdict,
                    "prove_path": report.prove_path,
                    "walk_forward_path": report.walk_forward_path,
                    "phase1_status": report.phase1_historical.status,
                    "phase2_status": report.phase2_paper.status,
                    "phase1_metrics": report.phase1_historical.metrics,
                    "phase2_metrics": report.phase2_paper.metrics,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    elif args.cmd == "demo-cluster":
        engine = ClusterEngine()
        alerts = engine.load_events(demo_events())
        print(json.dumps([{"mint": a.mint, "wallets": a.wallets, "score": a.score} for a in alerts], indent=2))

    elif args.cmd == "scan-mint":
        rpc = SolanaRpc(rpc_url(settings))
        hot_funders, session_notes, report_path = refresh_session_hot_funders(
            settings, prefer_cache=True, max_cache_age_sec=7 * 86400
        )
        intel = analyze_launch(
            args.mint,
            rpc,
            pair_address=args.pair,
            settings=settings,
            hot_funders=hot_funders,
        )
        intel.notes.extend(
            [
                f"session_hot_funders={len(hot_funders)}",
                f"session_report={report_path}",
                *session_notes,
            ]
        )
        print(json.dumps(_intel_dict(intel), indent=2, ensure_ascii=False))

    elif args.cmd == "trace-funders":
        from smartalpha.trace_funders import trace_mint_funders

        rpc = SolanaRpc(rpc_url(settings))
        report = trace_mint_funders(
            args.mint, rpc, pair_address=args.pair, max_buyers=args.max_buyers, settings=settings
        )
        out = _trace_dict(report)
        print(json.dumps(out, indent=2, ensure_ascii=False))

    elif args.cmd == "watch-launches":
        if args.once_mint:
            from smartalpha.launch_watch import process_new_mint

            rpc = SolanaRpc(
                rpc_url(settings)
            )
            hot, notes, path = refresh_session_hot_funders(
                settings, prefer_cache=True, max_cache_age_sec=7 * 86400
            )
            print(json.dumps({"session_funders": len(hot), "notes": notes, "path": str(path)}, indent=2))
            sig = process_new_mint(
                args.once_mint,
                "unknown",
                "manual",
                settings=settings,
                rpc=rpc,
                hot_funders=hot,
            )
            from smartalpha.paper_log import paper_health

            print(json.dumps(_launch_signal_dict(sig) if sig else {"skipped": True}, indent=2))
            print(json.dumps({"paper_health": paper_health(settings=settings)}, indent=2))
        else:
            import asyncio

            from smartalpha.launch_watch import watch_pump_launches

            asyncio.run(watch_pump_launches(settings))

    elif args.cmd == "discover-funders":
        from smartalpha.config import ROOT
        from smartalpha.discover_funders import (
            discover_funders,
            load_mint_list,
            write_discover_report,
        )

        path = Path(args.mints_file)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            path = ROOT / "mints.example.txt"
        mints = load_mint_list(path)
        if not mints:
            print(json.dumps({"error": f"no mints in {path}"}, indent=2))
            sys.exit(1)

        rpc = SolanaRpc(rpc_url(settings))
        report = discover_funders(
            mints,
            rpc,
            max_buyers=args.max_buyers,
            min_mint_hits=args.min_mints,
            settings=settings,
        )
        out_path = write_discover_report(report)
        out = {
            "mints_file": str(path),
            "mints_count": len(mints),
            "report_path": str(out_path),
            "recommended": report.recommended,
            "skipped_mints": report.skipped_mints,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))

    elif args.cmd == "auto-discover":
        from smartalpha.auto_discover import run_auto_discover, write_auto_discover_report
        from smartalpha.config import ROOT
        from smartalpha.mint_sources import find_candidate_mints

        if args.dry_run:
            candidates, source_notes = find_candidate_mints(
                settings, min_gain_pct=args.min_gain, limit=args.limit
            )
            out_path = ROOT / "data" / "auto_discover_candidates.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "source_notes": source_notes,
                "candidates": [
                    {
                        "mint": c.mint,
                        "source": c.source,
                        "gain_h24_pct": c.gain_h24_pct,
                        "pair": c.pair,
                        "url": c.url,
                    }
                    for c in candidates
                ],
            }
            out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            print(json.dumps({**payload, "report_path": str(out_path)}, indent=2, ensure_ascii=False))
        else:
            extra = Path(args.mints_file)
            if not extra.is_absolute():
                extra = ROOT / extra
            if not extra.exists():
                extra = None  # type: ignore[assignment]
            report = run_auto_discover(
                settings,
                min_gain_pct=args.min_gain,
                mint_limit=args.limit,
                min_mint_hits=args.min_mints,
                max_buyers=args.max_buyers,
                extra_mints_file=extra,
            )
            out_path = write_auto_discover_report(report)
            dr = report.discover
            out = {
                "report_path": str(out_path),
                "source_notes": report.source_notes,
                "candidates": len(report.candidates),
                "mints_traced": len(report.mints_traced),
                "recommended": getattr(dr, "recommended", []),
                "skipped_mints": getattr(dr, "skipped_mints", []),
                "notes": report.notes,
            }
            print(json.dumps(out, indent=2, ensure_ascii=False))

    elif args.cmd == "gmgn-cookie":
        from smartalpha.gmgn_cookie import (
            cookie_file_path,
            ensure_cookie,
            import_cookie,
            parse_cookie,
            test_cookie,
        )

        if args.gmgn_cmd == "import":
            raw = args.cookie
            if not raw:
                raw = sys.stdin.read().strip()
            if not raw:
                print(json.dumps({"error": "paste cookie as argument or stdin"}, indent=2))
                sys.exit(1)
            try:
                import_cookie(raw, settings)
            except ValueError as exc:
                print(json.dumps({"error": str(exc)}, indent=2))
                sys.exit(1)
            refreshed = ensure_cookie(settings)
            ok, msg = test_cookie(refreshed)
            print(
                json.dumps(
                    {
                        "saved_to": str(cookie_file_path(settings)),
                        "keys": list(parse_cookie(refreshed).keys()),
                        "test_ok": ok,
                        "test_message": msg,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            sys.exit(0 if ok else 1)
        elif args.gmgn_cmd == "refresh":
            raw = ensure_cookie(settings, persist=True)
            if not raw:
                print(json.dumps({"error": "no cookie file — run gmgn-cookie import first"}, indent=2))
                sys.exit(1)
            ok, msg = test_cookie(raw)
            print(json.dumps({"test_ok": ok, "test_message": msg}, indent=2, ensure_ascii=False))
            sys.exit(0 if ok else 1)
        elif args.gmgn_cmd == "test":
            raw = ensure_cookie(settings, persist=False)
            ok, msg = test_cookie(raw)
            print(json.dumps({"test_ok": ok, "test_message": msg}, indent=2, ensure_ascii=False))
            sys.exit(0 if ok else 1)


def _launch_signal_dict(sig) -> dict:
    return {
        "mint": sig.mint,
        "creator": sig.creator,
        "signature": getattr(sig, "signature", ""),
        "ts": getattr(sig, "ts", None),
        "recommendation": sig.recommendation,
        "copytrap_risk": sig.copytrap_risk,
        "hot_funder_hits": sig.hot_funder_hits,
        "bundler_wallets": sig.bundler_wallets,
        "top_funders": sig.top_funders,
        "notes": sig.notes,
    }


def _trace_dict(report) -> dict:
    return {
        "mint": report.mint,
        "buyers_traced": report.buyers_traced,
        "bundler_wallets": report.bundler_wallets,
        "notes": report.notes,
        "funders_ranked": [
            {
                "funder": h.funder,
                "buyer_count": h.count,
                "buyers": h.buyers,
                "bundler_signal": h.is_bundler_signal,
            }
            for h in report.funders
        ],
        "suggested_for_funders_json": report.suggested_hot,
    }


def _intel_dict(intel) -> dict:
    from smartalpha.signal_rules import classify_signal, hot_organic_buyers

    level = classify_signal(
        intel,
        min_hot_buyers=Settings().signal_min_hot_buyers,
        min_liquidity_usd=0,  # report level without live liq gate
        allow_unknown_liq=True,
    )
    return {
        "mint": intel.mint,
        "recommendation": intel.recommendation,
        "signal_level": level.value,
        "funder_injected": bool(getattr(intel, "funder_injected", False)),
        "copytrap_risk": intel.copytrap_risk,
        "hot_funder_hits": intel.hot_funder_hits,
        "hot_organic_buyers": len(hot_organic_buyers(intel)),
        "bundler_wallets": intel.bundler_wallets,
        "notes": intel.notes,
        "buyers": [
            {
                "wallet": b.wallet,
                "buy_sol": b.buy_sol,
                "follow_score": b.follow_score,
                "wallet_age_hours": b.wallet_age_hours,
                "funder": b.funder,
                "funder_known": b.funder_known,
                "flags": b.flags,
            }
            for b in intel.buyers[:15]
        ],
    }


def _report_dict(report) -> dict:
    return {
        "mint": report.mint,
        "score": report.score,
        "buy_sol": report.buy_sol,
        "sell_sol": report.sell_sol,
        "unique_sellers": report.unique_sellers,
        "unique_buyers": report.unique_buyers,
        "signals": [{"kind": s.kind, "detail": s.detail, "severity": s.severity} for s in report.signals],
    }


def _backtest_dict(res) -> dict:
    closed = res.wins + res.losses
    return {
        "trades_opened": res.trades,
        "closed": closed,
        "wins": res.wins,
        "losses": res.losses,
        "win_rate": round(res.wins / closed, 3) if closed else None,
        "net_sol": round(res.net_sol, 4),
        "gross_in_sol": round(res.gross_in, 4),
        "gross_out_sol": round(res.gross_out, 4),
        "by_wallet": {k: round(v, 4) for k, v in res.by_wallet.items()},
        "notes": res.notes,
    }


if __name__ == "__main__":
    main()
