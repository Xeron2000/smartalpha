"""Reviewer — checks Feature Timestamp / Leakage / Metric / Hypothesis consistency — GOAL V2 lineage."""
from __future__ import annotations

import time


def review_hypothesis(hypo: dict, snapshots: dict | None = None, oos_report: dict | None = None, dry_run: bool = False) -> dict:
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
    # GOAL V2 lineage checks: directly audit selected_mints
    details = (oos_report or {}).get("details") or {}
    selected = details.get("selected_mints") or []
    for sig in selected:
        mint = sig.get("mint", "?")
        feats = sig.get("features") or {}
        evidence_mode = sig.get("evidence_mode") or feats.get("evidence_mode") or "unknown"
        available_at = sig.get("available_at") or feats.get("available_at")
        observed_at = sig.get("feature_observed_at") or feats.get("observed_at")
        as_of_ts = sig.get("as_of_ts") or feats.get("as_of_ts")
        reconstructed_at = sig.get("reconstructed_at") or feats.get("reconstructed_at")
        entry_ts = sig.get("entry_ts")
        entry_candle_ts = sig.get("entry_candle_ts")
        source = feats.get("source") or sig.get("source")
        # synthetic only allowed in dry_run
        if not dry_run and source == "synthetic":
            issues.append(f"{mint}: synthetic source in live mode")
            passed = False
        if not dry_run and evidence_mode == "synthetic":
            issues.append(f"{mint}: synthetic evidence_mode in live mode")
            passed = False
        if evidence_mode == "prospective_snapshot":
            if observed_at is not None and entry_ts is not None and observed_at > entry_ts:
                issues.append(f"{mint}: observed_at {observed_at} > entry_ts {entry_ts} (prospective leakage)")
                passed = False
        elif evidence_mode == "historical_reconstruction":
            if as_of_ts is not None and entry_ts is not None and as_of_ts > entry_ts:
                issues.append(f"{mint}: as_of_ts {as_of_ts} > entry_ts {entry_ts}")
                passed = False
            if reconstructed_at is not None and as_of_ts is not None and reconstructed_at < as_of_ts:
                issues.append(f"{mint}: reconstructed_at {reconstructed_at} < as_of_ts {as_of_ts}")
                passed = False
        # available must be <= entry
        if available_at is not None and entry_ts is not None and available_at > entry_ts:
            issues.append(f"{mint}: available_at {available_at} > entry_ts {entry_ts}")
            passed = False
        if observed_at is not None and entry_ts is not None and observed_at > entry_ts:
            # for historical, observed is reconstructed_at which is now > entry, so only check prospective
            if evidence_mode == "prospective_snapshot":
                issues.append(f"{mint}: observed_at {observed_at} > entry_ts {entry_ts}")
                passed = False
        if entry_candle_ts is not None and entry_ts is not None and entry_candle_ts < entry_ts:
            issues.append(f"{mint}: entry_candle_ts {entry_candle_ts} < entry_ts {entry_ts} (pre-entry candle used)")
            passed = False
        if feats.get("window_complete") is False:
            issues.append(f"{mint}: window_complete false — HISTORICAL_INCOMPLETE")
            passed = False
    return {
        "hypothesis": hypo.get("name"),
        "passed": passed,
        "issues": issues,
        "source": "reviewer",
        "observed_at": int(time.time()),
        "checked_at": int(time.time()),
    }
