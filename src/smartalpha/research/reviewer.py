"""Reviewer — checks Feature Timestamp / Leakage / Metric / Hypothesis consistency."""
from __future__ import annotations

import time


def review_hypothesis(hypo: dict, snapshots: dict | None = None, oos_report: dict | None = None) -> dict:
    s = snapshots or {}
    issues: list[str] = []
    passed = True
    if not hypo.get("falsification_condition"):
        issues.append("missing falsification_condition")
        passed = False
    for feat in hypo.get("features", []):
        if "@300s" in feat or "@900s" in feat:
            if feat.split("@")[0] in hypo.get("entry_rule", ""):
                issues.append(f"look-ahead: {feat} used in entry_rule")
                passed = False
    if s:
        from smartalpha.research.snapshot import validate_snapshot_order

        ok, msg = validate_snapshot_order(s)
        if not ok:
            issues.append(f"snapshot order fail: {msg}")
            passed = False
    if oos_report and oos_report.get("train_funders", 0) < 2:
        issues.append("insufficient train funders (<2) — discovery not separated")
    return {
        "hypothesis": hypo.get("name"),
        "passed": passed,
        "issues": issues,
        "source": "reviewer",
        "observed_at": int(time.time()),
        "checked_at": int(time.time()),
    }
