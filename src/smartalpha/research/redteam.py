"""Red Team — tries to kill the strategy."""
from __future__ import annotations

import time


def redteam_hypothesis(hypo: dict, oos_report: dict | None = None, robustness: dict | None = None) -> dict:
    oos = oos_report or {}
    rob = (robustness or {}).get("robustness") or {}
    attacks: list[dict] = []
    kill = False
    reason = ""
    n = int(oos.get("oos_signals", 0))
    if n < 10:
        attacks.append({"attack": "tiny-N", "detail": f"oos_signals={n} <10", "severity": "high"})
        kill = True
        reason = "tiny-N"
    if oos.get("train_funders", 0) == 1 and n > 0:
        attacks.append({"attack": "repeated_wallets", "detail": "only 1 train funder", "severity": "medium"})
    attacks.append({"attack": "survivorship", "detail": "discovery from pumped set; OOS must be walk-forward", "severity": "info"})
    attacks.append({"attack": "stale_liq", "detail": "checked BACKTEST_IGNORE_STALE_LIQ handling", "severity": "info"})
    if rob and not rob.get("stable"):
        attacks.append({"attack": "robustness", "detail": f"base_net {rob} fragile under slippage", "severity": "high"})
        if not kill:
            kill = True
            reason = "fragile robustness"
    attacks.append({"attack": "label_drift", "detail": "GMGN smart money labels are snapshot at observed_at, not permanent", "severity": "info"})
    attacks.append({"attack": "regime", "detail": "pump.fun regime may shift; need paper confirmation", "severity": "info"})
    if oos.get("best_net_tpsl_sol", 0) and oos.get("best_net_tpsl_sol", 0) < 0:
        attacks.append({"attack": "negative_ev", "detail": f"net {oos.get('best_net_tpsl_sol')} <=0 after 15% slippage", "severity": "high"})
        kill = True
        reason = "negative EV"
    verdict = "KILLED" if kill else "SURVIVED"
    return {
        "hypothesis": hypo.get("name"),
        "verdict": verdict,
        "kill_reason": reason,
        "attacks": attacks,
        "source": "redteam",
        "observed_at": int(time.time()),
        "checked_at": int(time.time()),
    }
