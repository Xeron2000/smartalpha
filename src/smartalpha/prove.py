"""Strategy proof protocol: First-Principles OOS + live paper gates.

Verdict ladder (strict — capital deployment requires PROVEN):
  FALSIFIED          enough samples, EV negative
  INSUFFICIENT_DATA  samples too thin to decide
  PROMISING          historical OOS passes, paper incomplete
  PROVEN             historical OOS + live paper both pass
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from smartalpha.config import ROOT, Settings
from smartalpha.db import Store
from smartalpha.signal_rules import calculate_friction_net_gain

MIN_OOS_SIGNALS = 10
MIN_PAPER_STRICT = 30
MIN_WIN_RATE = 0.35


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    value: float | int | None = None
    threshold: float | int | None = None


@dataclass
class PhaseReport:
    name: str
    status: str  # pass | fail | incomplete
    gates: list[GateResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.status == "pass" and all(g.passed for g in self.gates)


@dataclass
class ProveReport:
    generated_at: int
    verdict: str
    reason: str
    phase1_historical: PhaseReport
    phase2_paper: PhaseReport
    next_actions: list[str] = field(default_factory=list)


def run_prove(
    mints_file: Path | None = None,
    *,
    settings: Settings | None = None,
    position_sol: float = 0.5,
    min_oos_signals: int = MIN_OOS_SIGNALS,
    min_paper_strict: int = MIN_PAPER_STRICT,
    limit: int = 0,
) -> ProveReport:
    """Evaluate STRATEGY_SPEC.md four pillars against OOS and live paper evidence."""
    s = settings or Settings()
    path = mints_file or (ROOT / "data" / "auto_discover_25.json")
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        path = ROOT / "data" / "auto_discover.json"

    # --- Phase 1: Historical OOS Evaluation ---
    gates_p1: list[GateResult] = []
    p1_notes: list[str] = []
    mints_data = []
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            mints_data = raw.get("candidates", []) if isinstance(raw, dict) else []
        except Exception as exc:
            p1_notes.append(f"read_err={exc}")

    if limit > 0:
        mints_data = mints_data[:limit]

    n_samples = len(mints_data)
    gates_p1.append(
        GateResult(
            name="min_samples",
            passed=n_samples >= min_oos_signals,
            detail=f"{n_samples} samples evaluated (need >={min_oos_signals})",
            value=n_samples,
            threshold=min_oos_signals,
        )
    )

    gains = []
    for m in mints_data:
        g = m.get("gain_h24_pct")
        if g is not None:
            net = calculate_friction_net_gain(float(g) / 100.0, 5000.0)
            gains.append(net)

    net_ev = statistics.mean(gains) if gains else 0.0
    win_rate = (sum(1 for x in gains if x > 0) / len(gains)) if gains else 0.0

    gates_p1.append(
        GateResult(
            name="net_ev_positive",
            passed=net_ev > 0,
            detail=f"Net EV after friction = {net_ev*100:+.2f}%",
            value=net_ev,
            threshold=0.0,
        )
    )
    gates_p1.append(
        GateResult(
            name="min_win_rate",
            passed=win_rate >= MIN_WIN_RATE,
            detail=f"Win rate = {win_rate*100:.1f}% (need >={MIN_WIN_RATE*100:.0f}%)",
            value=win_rate,
            threshold=MIN_WIN_RATE,
        )
    )

    p1_pass = all(g.passed for g in gates_p1)
    phase1 = PhaseReport(
        name="Phase 1: Historical First-Principles OOS",
        status="pass" if p1_pass else "fail",
        gates=gates_p1,
        metrics={"samples": n_samples, "net_ev_pct": round(net_ev * 100, 2), "win_rate": round(win_rate, 3)},
        notes=p1_notes,
    )

    # --- Phase 2: Live Paper Trading Evaluation ---
    store = Store(s.db_path)
    from smartalpha.paper_log import paper_health

    h = paper_health(settings=s, store=store)
    n_strict = h["strict_rows"]
    gates_p2: list[GateResult] = []

    gates_p2.append(
        GateResult(
            name="min_paper_strict_samples",
            passed=n_strict >= min_paper_strict,
            detail=f"{n_strict} strict paper rows recorded (need >={min_paper_strict})",
            value=n_strict,
            threshold=min_paper_strict,
        )
    )

    p2_pass = n_strict >= min_paper_strict
    phase2 = PhaseReport(
        name="Phase 2: Live Paper Execution",
        status="pass" if p2_pass else ("incomplete" if n_strict > 0 else "incomplete"),
        gates=gates_p2,
        metrics=h,
        notes=["Paper logs track dynamic price impact and delayed snapshots"],
    )

    # --- Final Verdict ---
    if phase1.status == "pass" and phase2.status == "pass":
        verdict = "PROVEN"
        reason = "Both historical OOS and live paper proof gates passed."
    elif phase1.status == "pass":
        verdict = "PROMISING"
        reason = f"Historical OOS passed; live paper has {n_strict}/{min_paper_strict} strict samples."
    elif not phase1.all_passed and n_samples >= min_oos_signals:
        verdict = "FALSIFIED"
        reason = "Historical OOS failed positive EV or win rate threshold."
    else:
        verdict = "INSUFFICIENT_DATA"
        reason = f"Insufficient sample data (OOS samples={n_samples})."

    return ProveReport(
        generated_at=int(time.time()),
        verdict=verdict,
        reason=reason,
        phase1_historical=phase1,
        phase2_paper=phase2,
        next_actions=[
            "Run watch-launches in background to accumulate strict paper samples",
            "Export paper CSV via 'paper-log export' to analyze net gains",
        ],
    )
