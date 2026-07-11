"""Strategy proof protocol: historical OOS + live paper gates.

Verdict ladder (strict — money requires PROVEN):
  FALSIFIED          enough samples, EV negative
  INSUFFICIENT_DATA  samples too thin to decide
  PROMISING          historical OOS passes, paper incomplete
  PROVEN             historical OOS + live paper both pass
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from smartalpha.backtest_funders import load_mints_with_pairs
from smartalpha.config import ROOT, Settings
from smartalpha.db import Store
from smartalpha.funder import HotFunder
from smartalpha.funder_score import FunderGrade, grade_rank, mint_gains_from_report
from smartalpha.session_funders import _parse_grade
from smartalpha.walk_forward import run_walk_forward, write_walk_forward_report

# --- hard gates (tunable via CLI later) ---
MIN_OOS_SIGNALS = 10
MIN_PAPER_STRICT = 30
MIN_TRAIN_FUNDERS = 2
MIN_OOS_NET_SOL = 0.0
MIN_WIN_RATE = 0.35
MAX_SINGLE_TRADE_SHARE = 0.80  # if one trade >80% of positive net → fragile
DEFAULT_SLIPPAGE = 0.15


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
    walk_forward_path: str | None = None
    prove_path: str | None = None


def run_prove(
    mints_file: Path | None = None,
    *,
    settings: Settings | None = None,
    train_ratio: float = 0.7,
    min_grade: str = "medium",
    position_sol: float = 0.5,
    min_oos_signals: int = MIN_OOS_SIGNALS,
    min_paper_strict: int = MIN_PAPER_STRICT,
    limit: int = 0,
) -> ProveReport:
    s = settings or Settings()
    path = mints_file or (ROOT / "data" / "auto_discover_25.json")
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        path = ROOT / "data" / "auto_discover.json"

    mints = load_mints_with_pairs(path)
    if limit > 0:
        mints = mints[:limit]

    # Phase 1: chronological walk-forward with discovery-aware funder grades
    wf = run_walk_forward(
        mints,
        settings=s,
        split_mode="chronological",
        train_ratio=train_ratio,
        position_sol=position_sol,
        mints_source=path,
        mint_gains=mint_gains_from_report(path),
    )
    # Re-score OOS with grade floor applied to train funders
    grade_floor = _parse_grade(min_grade)
    filtered_funders = [
        r
        for r in wf.train_funders
        if grade_rank(r.get("quality", {}).get("grade")) >= grade_rank(grade_floor)
    ]
    phase1 = _eval_phase1(
        wf,
        filtered_funders=filtered_funders,
        grade_floor=grade_floor,
        min_oos_signals=min_oos_signals,
        position_sol=position_sol,
        settings=s,
        mints=mints,
    )
    wf_path = write_walk_forward_report(wf)

    # Phase 2: live paper log
    phase2 = _eval_phase2(
        settings=s,
        min_paper_strict=min_paper_strict,
        slippage=s.backtest_slippage,
        position_sol=position_sol,
    )

    verdict, reason, actions = _decide(phase1, phase2, min_oos_signals, min_paper_strict)

    report = ProveReport(
        generated_at=int(time.time()),
        verdict=verdict,
        reason=reason,
        phase1_historical=phase1,
        phase2_paper=phase2,
        next_actions=actions,
        walk_forward_path=str(wf_path),
    )
    out = write_prove_report(report)
    report.prove_path = str(out)
    return report


def _eval_phase1(
    wf,
    *,
    filtered_funders: list[dict],
    grade_floor: FunderGrade,
    min_oos_signals: int,
    position_sol: float,
    settings: Settings,
    mints: list[tuple[str, str | None]],
) -> PhaseReport:
    from smartalpha.walk_forward import _run_compare_with_funders

    notes = list(wf.notes)
    notes.append(
        f"grade_filter={grade_floor.value}: "
        f"{len(filtered_funders)}/{len(wf.train_funders)} train funders kept"
    )
    notes.append(
        "caveat: DexScreener h1/h6/h24 proxy ≠ 90s delayed fill; "
        "historical phase can only PROMISE, not PROVE live alpha"
    )

    gates: list[GateResult] = []
    metrics: dict = {
        "train_mints": len(wf.train_mints),
        "test_mints": len(wf.test_mints),
        "train_funders_raw": len(wf.train_funders),
        "train_funders_filtered": len(filtered_funders),
        "grade_floor": grade_floor.value,
    }

    # Gate: enough train funders after quality filter
    gates.append(
        GateResult(
            name="train_funders_min",
            passed=len(filtered_funders) >= MIN_TRAIN_FUNDERS,
            detail=f"{len(filtered_funders)} funders grade>={grade_floor.value}",
            value=len(filtered_funders),
            threshold=MIN_TRAIN_FUNDERS,
        )
    )

    # Gate: test set non-empty
    gates.append(
        GateResult(
            name="test_mints_nonempty",
            passed=len(wf.test_mints) >= 3,
            detail=f"{len(wf.test_mints)} OOS mints",
            value=len(wf.test_mints),
            threshold=3,
        )
    )

    compare = None
    oos_signals = 0
    oos_scanned = 0
    oos_liq_f = 0
    exit_modes: dict = {}
    best_mode = best_net = best_wr = None
    best_n = 0

    if filtered_funders and wf.test_mints:
        # Prefer walk-forward test_compare when grade filter keeps (almost) all
        # train funders — avoids a second full RPC pass that can drift results.
        reuse = (
            wf.test_compare is not None
            and len(filtered_funders) >= max(1, int(0.9 * len(wf.train_funders)))
        )
        if reuse:
            tc = wf.test_compare
            oos_signals = int(tc.get("signals") or 0)
            oos_scanned = len(wf.test_mints)
            oos_liq_f = int(tc.get("liquidity_filtered") or 0)
            exit_modes = tc.get("modes") or {}
            notes.append("oos: reused walk-forward test_compare (grade filter kept ≥90% funders)")
            notes.extend(tc.get("notes") or [])
        else:
            hot_map = {
                r["address"]: HotFunder(
                    r["address"], r.get("label", ""), float(r.get("weight", 1.0))
                )
                for r in filtered_funders
            }
            pair_map = {m: p for m, p in mints}
            test_pairs = [(m, pair_map.get(m)) for m in wf.test_mints]
            compare = _run_compare_with_funders(
                test_pairs, hot_map, settings=settings, position_sol=position_sol
            )
            oos_signals = compare.signals
            oos_scanned = compare.mints_scanned
            oos_liq_f = compare.liquidity_filtered
            exit_modes = compare.modes
            notes.append("oos: re-ran compare with grade-filtered funders only")
            notes.extend(compare.notes)

        metrics["oos_signals"] = oos_signals
        metrics["oos_mints_scanned"] = oos_scanned
        metrics["oos_liquidity_filtered"] = oos_liq_f
        metrics["exit_modes"] = exit_modes

        best_mode, best_net, best_wr, best_n = _best_mode(exit_modes)
        metrics["best_exit_mode"] = best_mode
        metrics["best_net_tpsl_sol"] = best_net
        metrics["best_win_rate"] = best_wr
        metrics["best_closed_trades"] = best_n

        gates.append(
            GateResult(
                name="oos_signal_count",
                passed=oos_signals >= min_oos_signals,
                detail=f"{oos_signals} strict OOS signals (need >={min_oos_signals})",
                value=oos_signals,
                threshold=min_oos_signals,
            )
        )
        gates.append(
            GateResult(
                name="oos_net_positive",
                passed=best_net is not None and best_net > MIN_OOS_NET_SOL,
                detail=f"best mode {best_mode} net={best_net} SOL",
                value=best_net,
                threshold=MIN_OOS_NET_SOL,
            )
        )
        gates.append(
            GateResult(
                name="oos_win_rate_floor",
                passed=best_wr is not None and (best_wr >= MIN_WIN_RATE or (best_net or 0) > 1.0),
                detail=f"win_rate={best_wr} (floor {MIN_WIN_RATE}, or net>1 SOL overrides)",
                value=best_wr,
                threshold=MIN_WIN_RATE,
            )
        )
    else:
        metrics["oos_signals"] = 0
        gates.append(
            GateResult(
                name="oos_signal_count",
                passed=False,
                detail="no filtered funders or no test mints — cannot score OOS",
                value=0,
                threshold=min_oos_signals,
            )
        )
        gates.append(
            GateResult(
                name="oos_net_positive",
                passed=False,
                detail="skipped",
                value=None,
                threshold=MIN_OOS_NET_SOL,
            )
        )
        gates.append(
            GateResult(
                name="oos_win_rate_floor",
                passed=False,
                detail="skipped",
                value=None,
                threshold=MIN_WIN_RATE,
            )
        )

    # Funder quality snapshot
    grades = {}
    for r in wf.train_funders:
        g = (r.get("quality") or {}).get("grade", "unknown")
        grades[g] = grades.get(g, 0) + 1
    metrics["train_funder_grades"] = grades
    metrics["filtered_funder_quality"] = [
        {
            "address": r["address"][:12] + "...",
            "grade": (r.get("quality") or {}).get("grade"),
            "win_rate": (r.get("quality") or {}).get("win_rate"),
            "median_h24_pct": (r.get("quality") or {}).get("median_h24_pct"),
            "rug_rate": (r.get("quality") or {}).get("rug_rate"),
            "mint_outcomes": (r.get("quality") or {}).get("mint_outcomes"),
        }
        for r in filtered_funders[:15]
    ]

    hard = [g for g in gates if g.name in ("oos_net_positive", "oos_win_rate_floor")]
    sample = next(g for g in gates if g.name == "oos_signal_count")
    funder_ok = next(g for g in gates if g.name == "train_funders_min")

    if not sample.passed:
        status = "incomplete"
    elif not funder_ok.passed:
        status = "fail"
    elif all(g.passed for g in hard) and sample.passed:
        status = "pass"
    elif all(g.passed for g in hard) and not sample.passed:
        status = "incomplete"  # positive but underpowered
    else:
        # negative EV with enough samples → fail; thin samples → incomplete
        if sample.passed:
            status = "fail"
        else:
            status = "incomplete"

    # Special: positive EV but under-sampled → incomplete (promising path)
    if (
        status == "fail"
        and metrics.get("best_net_tpsl_sol") is not None
        and metrics["best_net_tpsl_sol"] > 0
        and not sample.passed
    ):
        status = "incomplete"
        notes.append("net positive but sample size below gate — do not treat as pass")

    if status == "fail" and metrics.get("best_net_tpsl_sol", 0) <= 0 and sample.passed:
        notes.append("OOS EV non-positive with adequate sample → historical thesis weak")

    return PhaseReport(
        name="phase1_historical_oos",
        status=status,
        gates=gates,
        metrics=metrics,
        notes=notes,
    )


def _eval_phase2(
    *,
    settings: Settings,
    min_paper_strict: int,
    slippage: float,
    position_sol: float,
) -> PhaseReport:
    store = Store(settings.db_path)
    rows = store.list_paper_signals(limit=2000)
    notes: list[str] = []
    gates: list[GateResult] = []
    metrics: dict = {"paper_rows_total": len(rows)}

    if not rows:
        notes.append(
            "paper_signals table empty — watch-launches never persisted paper rows "
            "(seen_mints may exist from older runs before paper_log, or log errors)"
        )
        notes.append(
            "required: run `uv run smartalpha watch-launches` continuously ≥14–30 days"
        )
        gates.append(
            GateResult(
                name="paper_strict_count",
                passed=False,
                detail="0 paper rows",
                value=0,
                threshold=min_paper_strict,
            )
        )
        gates.append(
            GateResult(
                name="paper_ev_positive",
                passed=False,
                detail="no data",
                value=None,
                threshold=0,
            )
        )
        gates.append(
            GateResult(
                name="delay_tax_measured",
                passed=False,
                detail="need price_t0 + delayed price_usd snapshots",
                value=None,
                threshold=1,
            )
        )
        return PhaseReport(
            name="phase2_live_paper",
            status="incomplete",
            gates=gates,
            metrics=metrics,
            notes=notes,
        )

    strict = [r for r in rows if r.get("strict_signal")]
    metrics["paper_strict"] = len(strict)
    metrics["paper_follow_cohort"] = sum(
        1 for r in rows if r.get("recommendation") == "follow_cohort"
    )

    # Delay tax + paper EV from price ratios (not Dex rolling gain_m5)
    delay_keys = [str(d) for d in (settings.paper_snapshot_delays or (90, 180, 300, 900)) if d > 0]
    delay_stats: dict[str, dict] = {}
    pnls_by_delay: dict[str, list[float]] = {k: [] for k in delay_keys}

    for row in strict:
        p0 = row.get("price_usd")
        snaps = {}
        try:
            snaps = json.loads(row.get("snapshots_json") or "{}")
        except json.JSONDecodeError:
            continue
        if not p0 or float(p0) <= 0:
            # try snapshot 0
            s0 = snaps.get("0") or {}
            p0 = s0.get("price_usd")
        if not p0 or float(p0) <= 0:
            continue
        p0 = float(p0)
        for dk in delay_keys:
            snap = snaps.get(dk) or {}
            px = snap.get("price_usd")
            if px is None or float(px) <= 0:
                continue
            gain_pct = (float(px) / p0 - 1.0) * 100.0
            # entry slip + exit slip approximation
            pnl = position_sol * (1 + gain_pct / 100.0) * (1 - slippage) - position_sol
            # also charge entry slip: worse fill
            pnl = position_sol * ((1 - slippage) * (1 + gain_pct / 100.0) * (1 - slippage) - 1)
            pnls_by_delay[dk].append(pnl)
            delay_stats.setdefault(dk, {"gains": []})["gains"].append(gain_pct)

    for dk, st in delay_stats.items():
        gains = st["gains"]
        st["n"] = len(gains)
        st["median_gain_pct"] = round(statistics.median(gains), 2) if gains else None
        st["mean_gain_pct"] = round(statistics.mean(gains), 2) if gains else None
        pnls = pnls_by_delay[dk]
        st["net_pnl_sol"] = round(sum(pnls), 4) if pnls else None
        st["win_rate"] = (
            round(sum(1 for x in pnls if x >= 0) / len(pnls), 3) if pnls else None
        )
        del st["gains"]

    metrics["delay_tax"] = delay_stats

    # Prefer 300s (5m) as paper horizon; fallback 900s then 90s
    primary = None
    for prefer in ("300", "900", "180", "90"):
        if prefer in delay_stats and delay_stats[prefer].get("n", 0) > 0:
            primary = prefer
            break
    metrics["paper_primary_horizon_sec"] = int(primary) if primary else None
    paper_net = delay_stats.get(primary, {}).get("net_pnl_sol") if primary else None
    paper_n = delay_stats.get(primary, {}).get("n", 0) if primary else 0

    gates.append(
        GateResult(
            name="paper_strict_count",
            passed=len(strict) >= min_paper_strict,
            detail=f"{len(strict)} strict paper signals (need >={min_paper_strict})",
            value=len(strict),
            threshold=min_paper_strict,
        )
    )
    gates.append(
        GateResult(
            name="paper_ev_positive",
            passed=paper_net is not None and paper_net > 0 and paper_n >= 5,
            detail=f"horizon={primary}s net={paper_net} SOL n={paper_n}",
            value=paper_net,
            threshold=0,
        )
    )
    measured = any(st.get("n", 0) >= 5 for st in delay_stats.values())
    gates.append(
        GateResult(
            name="delay_tax_measured",
            passed=measured,
            detail="median price change from signal t0 across delays",
            value=sum(st.get("n", 0) for st in delay_stats.values()),
            threshold=5,
        )
    )

    if len(strict) < min_paper_strict:
        status = "incomplete"
    elif paper_net is not None and paper_net <= 0 and paper_n >= min_paper_strict // 2:
        status = "fail"
    elif paper_net is not None and paper_net > 0 and len(strict) >= min_paper_strict:
        status = "pass"
    else:
        status = "incomplete"

    return PhaseReport(
        name="phase2_live_paper",
        status=status,
        gates=gates,
        metrics=metrics,
        notes=notes,
    )


def _best_mode(
    modes: dict,
) -> tuple[str | None, float | None, float | None, int]:
    best_mode = None
    best_net = None
    best_wr = None
    best_n = 0
    for mode, stats in modes.items():
        net = float(stats.get("net_tpsl_sol") or 0)
        wins = int(stats.get("wins") or 0)
        losses = int(stats.get("losses") or 0)
        n = wins + losses
        wr = wins / n if n else None
        if best_net is None or net > best_net:
            best_mode, best_net, best_wr, best_n = mode, net, wr, n
    return best_mode, best_net, best_wr, best_n


def _decide(
    phase1: PhaseReport,
    phase2: PhaseReport,
    min_oos: int,
    min_paper: int,
) -> tuple[str, str, list[str]]:
    actions: list[str] = []

    if phase1.status == "fail" and phase2.status == "fail":
        return (
            "FALSIFIED",
            "Historical OOS and live paper both fail EV gates",
            [
                "Stop live capital. Revisit thesis: funder definition, settle delay, entry rules.",
                "Run param_sweep only as research, not as license to trade.",
            ],
        )

    if phase1.status == "fail":
        actions = [
            "Do not size up. Historical OOS EV failed with available samples.",
            "Inspect train_funder_grades — drop farm/distributors (high rug, low win_rate).",
            f"If OOS signals < {min_oos}, collect more mints via auto-discover then re-run prove.",
            "Optional: test settle 15–30s branch separately (different strategy).",
        ]
        return (
            "FALSIFIED" if phase1.metrics.get("oos_signals", 0) >= min_oos else "INSUFFICIENT_DATA",
            "Phase1 historical OOS did not pass",
            actions,
        )

    if phase1.status == "pass" and phase2.status == "pass":
        return (
            "PROVEN",
            "Historical OOS + live paper EV both pass gates — min size live OK with hard risk limits",
            [
                "Enable half-auto execution only on STRONG signals",
                "Position ≤0.1–0.2 SOL, daily loss kill-switch",
                "Re-run prove weekly; auto-pause if rolling paper EV turns negative",
            ],
        )

    if phase1.status == "pass" and phase2.status != "pass":
        actions = [
            "Phase1 only → PROMISING, not bankable. Start continuous paper:",
            "  uv run smartalpha watch-launches",
            "  # cron every 10m: uv run smartalpha paper-log catch-up",
            f"Collect ≥{min_paper} strict paper signals with full delay snapshots",
            "Re-run: uv run smartalpha prove",
            "Do not deploy real size until verdict=PROVEN",
        ]
        return (
            "PROMISING",
            "Historical OOS passes; live paper incomplete or failing",
            actions,
        )

    # phase1 incomplete
    actions = [
        "Expand mint universe: uv run smartalpha auto-discover --min-gain 200 --limit 40",
        "Re-run prove on the larger auto_discover report",
        "In parallel start paper: uv run smartalpha watch-launches",
        f"Need ≥{min_oos} OOS signals and grade≥medium funders after filter",
    ]
    if phase2.status == "fail":
        actions.insert(
            0,
            "Live paper EV already negative — treat as red flag even before OOS fills in",
        )
        return (
            "FALSIFIED",
            "Paper EV negative while historical still underpowered",
            actions,
        )
    return (
        "INSUFFICIENT_DATA",
        "Not enough OOS signals / quality funders to accept or reject the thesis",
        actions,
    )


def write_prove_report(report: ProveReport, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "prove_report.json"
    p.parent.mkdir(parents=True, exist_ok=True)

    def phase_dict(ph: PhaseReport) -> dict:
        return {
            "name": ph.name,
            "status": ph.status,
            "gates": [asdict(g) for g in ph.gates],
            "metrics": ph.metrics,
            "notes": ph.notes,
        }

    payload = {
        "generated_at": report.generated_at,
        "verdict": report.verdict,
        "reason": report.reason,
        "phase1_historical": phase_dict(report.phase1_historical),
        "phase2_paper": phase_dict(report.phase2_paper),
        "next_actions": report.next_actions,
        "walk_forward_path": report.walk_forward_path,
        "protocol": {
            "min_oos_signals": MIN_OOS_SIGNALS,
            "min_paper_strict": MIN_PAPER_STRICT,
            "min_train_funders": MIN_TRAIN_FUNDERS,
            "min_win_rate": MIN_WIN_RATE,
            "slippage_assumed": "settings.backtest_slippage (default 15%)",
            "price_proxy": "DexScreener h1/h6/h24 for OOS; paper uses price_usd ratios",
        },
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p


def prove_summary_text(report: ProveReport) -> str:
    lines = [
        f"VERDICT: {report.verdict}",
        f"REASON:  {report.reason}",
        "",
        f"Phase1 ({report.phase1_historical.status}): historical walk-forward OOS",
    ]
    for g in report.phase1_historical.gates:
        mark = "PASS" if g.passed else "FAIL"
        lines.append(f"  [{mark}] {g.name}: {g.detail}")
    m1 = report.phase1_historical.metrics
    if m1.get("best_exit_mode"):
        lines.append(
            f"  best_exit={m1.get('best_exit_mode')} "
            f"net={m1.get('best_net_tpsl_sol')} SOL "
            f"wr={m1.get('best_win_rate')} n={m1.get('best_closed_trades')}"
        )
    lines.append(
        f"  funders raw/filtered={m1.get('train_funders_raw')}/{m1.get('train_funders_filtered')} "
        f"grades={m1.get('train_funder_grades')}"
    )
    lines.append("")
    lines.append(f"Phase2 ({report.phase2_paper.status}): live paper")
    for g in report.phase2_paper.gates:
        mark = "PASS" if g.passed else "FAIL"
        lines.append(f"  [{mark}] {g.name}: {g.detail}")
    lines.append("")
    lines.append("NEXT:")
    for a in report.next_actions:
        lines.append(f"  - {a}")
    if report.prove_path:
        lines.append(f"\nReport: {report.prove_path}")
    return "\n".join(lines)
