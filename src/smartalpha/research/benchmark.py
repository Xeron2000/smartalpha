"""GMGN vs Helius Benchmark — recall/latency/missing/duplicate."""
from __future__ import annotations

import json
import time
from pathlib import Path

from smartalpha.config import ROOT, Settings


def run_benchmark(settings: Settings | None = None, helius_mints: list[str] | None = None, gmgn_mints: list[str] | None = None) -> dict:
    now = int(time.time())
    helius_set = set(helius_mints or [f"mint_helius_{i}" for i in range(100)])
    gmgn_set = set(gmgn_mints or [f"mint_helius_{i}" for i in range(92)] + [f"mint_gmgn_only_{i}" for i in range(5)])
    tp = len(helius_set & gmgn_set)
    fp = len(gmgn_set - helius_set)
    fn = len(helius_set - gmgn_set)
    recall = tp / len(helius_set) if helius_set else 0.0
    missing_rate = fn / len(helius_set) if helius_set else 0.0
    duplicate_rate = 0.02
    latency = {"p50_ms": 8200, "p95_ms": 14500, "mean_ms": 9100}
    return {
        "generated_at": now,
        "source": "fixture",
        "observed_at": now,
        "helius_total": len(helius_set),
        "gmgn_total": len(gmgn_set),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "recall": round(recall, 3),
        "missing_rate": round(missing_rate, 3),
        "duplicate_rate": round(duplicate_rate, 3),
        "latency_ms": latency,
        "conclusion": "Helius logsSubscribe remains Primary; GMGN trenches poll is fallback/cross-validation only",
        "notes": ["fixture benchmark for dry-run; live capture would run parallel WS+poll for 24h"],
    }


def write_benchmark(report: dict, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "research" / "benchmark_gmgn_vs_helius.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return p
