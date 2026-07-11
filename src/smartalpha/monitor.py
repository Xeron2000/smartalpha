from __future__ import annotations

import json
import time

from smartalpha.cluster import ClusterEngine
from smartalpha.config import Settings, load_wallets
from smartalpha.copytrap import check_copytrap
from smartalpha.db import Store
from smartalpha.dump_detect import analyze_dump
from smartalpha.rpc import SolanaRpc, parse_wallet_swaps
from smartalpha.telegram import notify_cluster, notify_dump
from smartalpha.types import TradeEvent, WalletConfig


class Monitor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.store = Store(self.settings.db_path)
        self.rpc = SolanaRpc(self.settings.rpc_url)
        self.cluster = ClusterEngine(
            window_sec=self.settings.cluster_window,
            min_wallets=self.settings.cluster_min_wallets,
            min_score=self.settings.cluster_min_score,
        )
        self.wallets = {w.address: w for w in load_wallets()}

    def poll_once(self) -> dict[str, int]:
        stats = {"wallets": 0, "events": 0, "clusters": 0, "dumps": 0}
        now = int(time.time())
        for addr, wcfg in self.wallets.items():
            stats["wallets"] += 1
            sigs = self.rpc.get_signatures(addr, limit=15)
            if not sigs:
                continue
            last = self.store.get_last_sig(addr)
            new_sigs = []
            for s in sigs:
                if s["signature"] == last:
                    break
                if s.get("err"):
                    continue
                new_sigs.append(s["signature"])
            if sigs and not last:
                # first run: only process newest to avoid backfill flood
                new_sigs = [sigs[0]["signature"]] if sigs else []

            if sigs:
                self.store.set_last_sig(addr, sigs[0]["signature"])

            for sig in reversed(new_sigs):
                tx = self.rpc.get_transaction(sig)
                for raw in parse_wallet_swaps(tx, addr):
                    ev = _attach_wallet_meta(raw, wcfg)
                    if not self.store.save_event(ev):
                        continue
                    stats["events"] += 1
                    alert = self.cluster.ingest(ev, now=ev.ts)
                    if alert:
                        stats["clusters"] += 1
                        # copy-trap: skip if leader buy looks like bait
                        trap = check_copytrap(
                            alert.wallets[0],
                            [e for e in self.store.events_since(ev.ts - 60) if e.mint == alert.mint],
                        )
                        if not trap.safe_to_mirror:
                            print(f"cluster suppressed (copytrap={trap.risk}): {trap.reasons}")
                            continue
                        payload = _cluster_payload(alert)
                        self.store.save_alert("cluster", alert.mint, payload, alert.ts)
                        notify_cluster(payload, self.settings)
                    if ev.side.value == "sell":
                        window = self.store.events_since(now - self.settings.cluster_window)
                        report = analyze_dump(window, ev.mint, settings=self.settings)
                        if report.score >= 60:
                            stats["dumps"] += 1
                            payload = _dump_payload(report)
                            self.store.save_alert("dump", ev.mint, payload, ev.ts)
                            notify_dump(payload, self.settings)
        return stats

    def run_forever(self) -> None:
        if not self.wallets:
            raise SystemExit(
                "No wallets configured. Copy wallets.example.json → wallets.json and add addresses."
            )
        print(f"Monitoring {len(self.wallets)} wallets, poll={self.settings.poll_interval}s")
        while True:
            try:
                stats = self.poll_once()
                if stats["events"] or stats["clusters"] or stats["dumps"]:
                    print(f"poll: {stats}")
            except Exception as exc:  # ponytail: keep loop alive on RPC blips
                print(f"poll error: {exc}")
            time.sleep(self.settings.poll_interval)


def _attach_wallet_meta(ev: TradeEvent, w: WalletConfig) -> TradeEvent:
    return TradeEvent(
        wallet=ev.wallet,
        mint=ev.mint,
        side=ev.side,
        sol_delta=ev.sol_delta,
        token_delta=ev.token_delta,
        signature=ev.signature,
        ts=ev.ts,
        tier=w.tier,
        weight=w.weight,
    )


def _cluster_payload(alert) -> str:
    return json.dumps(
        {
            "mint": alert.mint,
            "wallets": alert.wallets,
            "score": round(alert.score, 2),
            "side": alert.side.value,
        },
        ensure_ascii=False,
    )


def _dump_payload(report) -> str:
    return json.dumps(
        {
            "mint": report.mint,
            "score": report.score,
            "buy_sol": round(report.buy_sol, 4),
            "sell_sol": round(report.sell_sol, 4),
            "sellers": report.unique_sellers,
            "signals": [s.kind for s in report.signals if s.severity != "low"],
        },
        ensure_ascii=False,
    )
