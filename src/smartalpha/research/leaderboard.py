from __future__ import annotations

import json
import time
from pathlib import Path

from smartalpha.config import ROOT


def build_leaderboard(results: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for name, res in results.items():
        hist = res.get("historical") or res.get("oos") or {}
        rob = res.get("robustness") or {}
        # EV = net / executed (priced and executed), not net / oos_signals
        details = hist.get("details") or {}
        executed = int(details.get("executed", hist.get("oos_signals", 0)) or 0)
        ev = float(hist.get("best_net_tpsl_sol", 0) or 0) / max(1, executed)
        rows.append(
            {
                "hypothesis": name,
                "oos_signals": int(hist.get("oos_signals", 0)),
                "oos_net": float(hist.get("best_net_tpsl_sol", 0) or 0),
                "ev": round(ev, 4),
                "win_rate": float(hist.get("best_win_rate", 0) or 0),
                "robust_passed": bool((rob.get("robustness") or rob).get("stable", rob.get("passed", False))),
                "source": "leaderboard",
                "observed_at": int(time.time()),
            }
        )
    rows.sort(key=lambda r: r["ev"], reverse=True)
    return rows


def write_leaderboard(rows: list[dict], path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "research" / "leaderboard.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": int(time.time()),
        "source": "leaderboard",
        "observed_at": int(time.time()),
        "rows": rows,
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p
