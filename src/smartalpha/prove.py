"""Evidence-gated strategy proof: observable entry features + executable paper outcomes."""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smartalpha.config import ROOT, Settings
from smartalpha.db import Store
from smartalpha.exit_rules import exit_policy, simulate_exit
from smartalpha.launch_intel import BuyerProfile, LaunchIntel
from smartalpha.paper_log import paper_health
from smartalpha.signal_rules import calculate_friction_net_gain, should_follow_launch

MIN_OOS_SIGNALS = 10
MIN_PAPER_STRICT = 30
MIN_WIN_RATE = 0.35
PAPER_PROOF_DELAY_SEC = 300


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
    position_sol: float | None = None,
    min_oos_signals: int = MIN_OOS_SIGNALS,
    min_paper_strict: int = MIN_PAPER_STRICT,
    limit: int = 0,
) -> ProveReport:
    """Run the same strict entry gate used by live code, then simulate exits.

    Historical records must contain the features observed at signal time. They
    are still proxy evidence unless the outcome contains an actual price path.
    Live paper evidence is the only route to ``PROVEN``.
    """
    s = settings or Settings()
    position = position_sol if position_sol is not None else s.execution_trade_size_sol
    path = mints_file or (ROOT / "data" / "auto_discover_25.json")
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        path = ROOT / "data" / "auto_discover.json"

    raw: Any = {}
    records: list[dict] = []
    p1_notes: list[str] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            records = raw.get("candidates", []) if isinstance(raw, dict) else []
        except Exception as exc:
            p1_notes.append(f"read_err={type(exc).__name__}: {exc}")
    else:
        p1_notes.append(f"missing dataset: {path}")

    if limit > 0:
        records = records[:limit]

    dataset_oos = _is_oos_dataset(raw, records)
    if not dataset_oos:
        p1_notes.append("dataset is not explicitly marked OOS; no historical promotion")

    outcomes: list[float] = []
    skipped: list[str] = []
    signal_count = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            skipped.append(f"record[{index}]: not an object")
            continue
        intel, features, missing = _record_to_intel(record)
        if missing:
            skipped.append(f"record[{index}]: missing {','.join(missing)}")
            continue
        strict = should_follow_launch(
            intel,
            min_unique_buyers=s.signal_min_unique_buyers,
            min_liquidity_usd=s.signal_min_liquidity_usd,
            liquidity_usd=features["liquidity_usd"],
            volume_usd=features["volume_usd"],
            min_velocity=s.signal_min_velocity,
            min_buy_sell_ratio=s.signal_min_buy_sell_ratio,
            max_buyer_share=s.signal_max_buyer_share,
            allow_unknown_liq=False,
            allow_unknown_velocity=False,
        )
        if not strict:
            continue
        signal_count += 1

        gains = _record_gains(record)
        if gains.get("h24") is None:
            skipped.append(f"record[{index}]: missing gain_h24_pct")
            continue
        try:
            pnl, _reason = simulate_exit(
                gains,
                position,
                s.execution_max_slippage_pct / 100.0,
                policy=exit_policy(s),
            )
        except (TypeError, ValueError) as exc:
            skipped.append(f"record[{index}]: exit_error={type(exc).__name__}")
            continue
        if pnl is not None:
            outcomes.append(pnl / max(position, 1e-12))

    net_ev = statistics.mean(outcomes) if outcomes else 0.0
    win_rate = sum(1 for value in outcomes if value > 0) / len(outcomes) if outcomes else 0.0
    gates_p1 = [
        GateResult(
            "oos_dataset",
            dataset_oos,
            "dataset explicitly marked OOS" if dataset_oos else "dataset split is unknown",
            1 if dataset_oos else 0,
            1,
        ),
        GateResult(
            "min_signals",
            len(outcomes) >= min_oos_signals,
            f"{len(outcomes)} strict outcomes (need >={min_oos_signals})",
            len(outcomes),
            min_oos_signals,
        ),
        GateResult(
            "net_ev_positive",
            net_ev > 0,
            f"net EV after configured friction = {net_ev * 100:+.2f}%",
            net_ev,
            0.0,
        ),
        GateResult(
            "min_win_rate",
            win_rate >= MIN_WIN_RATE,
            f"win rate = {win_rate * 100:.1f}% (need >={MIN_WIN_RATE * 100:.0f}%)",
            win_rate,
            MIN_WIN_RATE,
        ),
    ]
    p1_pass = all(g.passed for g in gates_p1)
    phase1 = PhaseReport(
        name="Phase 1: Historical First-Principles OOS",
        status="pass" if p1_pass else ("incomplete" if not dataset_oos else "fail"),
        gates=gates_p1,
        metrics={
            "records": len(records),
            "strict_signals": signal_count,
            "closed_outcomes": len(outcomes),
            "net_ev_pct": round(net_ev * 100, 4),
            "win_rate": round(win_rate, 4),
        },
        notes=p1_notes + skipped[:20],
    )

    store = Store(s.db_path)
    health = paper_health(settings=s, store=store)
    paper_rows = store.list_paper_signals(limit=2000)
    paper_returns, paper_sources = _paper_returns(
        paper_rows,
        delay_sec=PAPER_PROOF_DELAY_SEC,
        trade_size_usd=s.paper_trade_size_usd,
    )
    paper_ev = statistics.mean(paper_returns) if paper_returns else 0.0
    paper_win_rate = (
        sum(1 for value in paper_returns if value > 0) / len(paper_returns)
        if paper_returns
        else 0.0
    )
    exact_sources = {"onchain_quote", "signer_quote"}
    exact_source_ok = bool(paper_sources) and paper_sources <= exact_sources
    p2_notes = [
        "paper returns are measured from signal t0 to the 300s snapshot",
        "DexScreener snapshots remain proxy evidence and cannot promote PROVEN",
    ]
    gates_p2 = [
        GateResult(
            "min_paper_strict_samples",
            health["strict_rows"] >= min_paper_strict,
            f"{health['strict_rows']} strict paper rows (need >={min_paper_strict})",
            health["strict_rows"],
            min_paper_strict,
        ),
        GateResult(
            "min_complete_returns",
            len(paper_returns) >= min_paper_strict,
            f"{len(paper_returns)} strict rows have t0/{PAPER_PROOF_DELAY_SEC}s returns",
            len(paper_returns),
            min_paper_strict,
        ),
        GateResult(
            "paper_net_ev_positive",
            paper_ev > 0,
            f"paper net EV = {paper_ev * 100:+.2f}%",
            paper_ev,
            0.0,
        ),
        GateResult(
            "execution_grade_quotes",
            exact_source_ok,
            "all returns use executable quotes" if exact_source_ok else "quotes are missing or provider proxies",
            1 if exact_source_ok else 0,
            1,
        ),
    ]
    p2_pass = all(g.passed for g in gates_p2)
    phase2 = PhaseReport(
        name="Phase 2: Live Paper Execution",
        status="pass" if p2_pass else "incomplete",
        gates=gates_p2,
        metrics={
            **health,
            "complete_returns": len(paper_returns),
            "net_ev_300s_pct": round(paper_ev * 100, 4),
            "win_rate_300s": round(paper_win_rate, 4),
            "quote_sources": sorted(paper_sources),
        },
        notes=p2_notes,
    )

    if phase1.all_passed and phase2.all_passed:
        verdict = "PROVEN"
        reason = "Historical OOS and execution-grade live paper gates passed."
    elif phase1.all_passed:
        verdict = "PROMISING"
        reason = f"Historical OOS passed; paper evidence is {len(paper_returns)}/{min_paper_strict} complete."
    elif records and dataset_oos and len(outcomes) >= min_oos_signals:
        verdict = "FALSIFIED"
        reason = "Historical OOS failed EV or win-rate gates."
    else:
        verdict = "INSUFFICIENT_DATA"
        reason = f"Insufficient valid evidence (records={len(records)}, strict outcomes={len(outcomes)})."

    return ProveReport(
        generated_at=int(time.time()),
        verdict=verdict,
        reason=reason,
        phase1_historical=phase1,
        phase2_paper=phase2,
        next_actions=[
            "generate an explicitly OOS, feature-complete candidate dataset",
            "run watch-launches in paper mode and collect executable 300s quotes",
            "do not arm canary until both phases pass",
        ],
    )


def _record_to_intel(record: dict) -> tuple[LaunchIntel, dict[str, float], list[str]]:
    missing: list[str] = []
    unique = _number(record, "unique_buyers")
    if unique is None:
        missing.append("unique_buyers")
        unique = 0
    wallets = [f"observed-buyer-{i}" for i in range(int(unique))]

    buy_count = _number(record, "buy_count")
    sell_count = _number(record, "sell_count")
    liquidity = _number(record, "liquidity_usd")
    volume = _number(record, "volume_m5_usd")
    top_buyer_share = _number(record, "top_buyer_share")
    pair = record.get("pair_address")
    copytrap = record.get("copytrap_risk")
    observed_at = _number(record, "features_observed_at")
    signal_ts = _number(record, "signal_ts")

    for name, value in (
        ("buy_count", buy_count),
        ("sell_count", sell_count),
        ("liquidity_usd", liquidity),
        ("volume_usd", volume),
        ("top_buyer_share", top_buyer_share),
        ("pair_address", pair),
        ("copytrap_risk", copytrap),
        ("features_observed_at", observed_at),
        ("signal_ts", signal_ts),
    ):
        if value is None:
            missing.append(name)
    if copytrap is not None and str(copytrap).lower() == "unknown":
        missing.append("copytrap_risk")
    if observed_at is not None and signal_ts is not None and observed_at > signal_ts:
        missing.append("lookahead_free_features")

    intel = LaunchIntel(
        mint=str(record.get("mint") or "unknown"),
        buyers=[BuyerProfile(wallet=w, buy_sol=0.01, ts=int(signal_ts or 0), signature="dataset") for w in wallets],
        bundler_wallets=[],
        copytrap_risk=str(copytrap or "unknown"),
        recommendation="dataset",
        buy_count=int(buy_count or 0),
        sell_count=int(sell_count or 0),
        top_buyer_share=float(top_buyer_share or 0.0),
        notes=[] if pair else ["no dex pair"],
        as_of_ts=int(signal_ts) if signal_ts is not None else None,
        launch_ts=None,
    )
    features = {
        "liquidity_usd": float(liquidity or 0),
        "volume_usd": float(volume or 0),
    }
    return intel, features, missing


def _record_gains(record: dict) -> dict[str, float | None]:
    gains: dict[str, float | None] = {}
    for key in ("h1", "h6", "h24"):
        value = record.get(f"gain_{key}_pct")
        try:
            gains[key] = float(value) if value is not None else None
        except (TypeError, ValueError):
            gains[key] = None
    return gains


def _paper_returns(
    rows: list[dict], *, delay_sec: int, trade_size_usd: float
) -> tuple[list[float], set[str]]:
    returns: list[float] = []
    sources: set[str] = set()
    for row in rows:
        if not row.get("strict_signal"):
            continue
        try:
            snapshots = json.loads(row.get("snapshots_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        snap0 = snapshots.get("0") or {}
        snapn = snapshots.get(str(delay_sec)) or {}
        p0 = row.get("price_usd") or snap0.get("price_usd")
        pn = snapn.get("price_usd")
        liq = snapn.get("liquidity_usd") or row.get("liquidity_usd")
        if p0 is None or pn is None or liq is None or float(p0) <= 0:
            continue
        source = str(snapn.get("source") or snap0.get("source") or "unknown")
        sources.add(source)
        gross = float(pn) / float(p0) - 1.0
        returns.append(calculate_friction_net_gain(gross, float(liq), trade_size_usd=trade_size_usd))
    return returns, sources


def _number(record: dict, *keys: str) -> float | None:
    for key in keys:
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_oos_dataset(raw: Any, records: list[dict]) -> bool:
    if not isinstance(raw, dict):
        return False
    metadata = raw.get("metadata")
    return isinstance(metadata, dict) and str(metadata.get("split") or "").lower() in {"oos", "test"}
