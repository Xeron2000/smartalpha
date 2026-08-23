"""Research cycle Orchestrator: Memory → Hypotheses → Runner → Reviewer → RedTeam → Leaderboard → Persist."""
from __future__ import annotations

import json
import time

from smartalpha.config import ROOT, Settings
from smartalpha.research.benchmark import run_benchmark, write_benchmark
from smartalpha.research.hypothesis import generate_hypotheses, write_hypotheses
from smartalpha.research.leaderboard import build_leaderboard, write_leaderboard
from smartalpha.research.memory import load_memory, save_memory
from smartalpha.research.redteam import redteam_hypothesis
from smartalpha.research.reviewer import review_hypothesis
from smartalpha.research.runner import run_all
from smartalpha.research.snapshot import capture_launch_snapshots


def run_cycle(settings: Settings | None = None, dry_run: bool = False) -> dict:
    s = settings or Settings()
    now = int(time.time())
    mem = load_memory()
    hypos = generate_hypotheses(mem, limit=3)
    write_hypotheses(hypos)
    # Live mode must not silently use fixture — dry_run is the only gate
    try:
        results = run_all(hypos, settings=s, dry_run=dry_run)
    except Exception as exc:
        # fail-closed: do not produce PROMISING leaderboard from fixture
        from smartalpha.research.runner import ExperimentError

        err_path = ROOT / "data" / "research" / "runs" / f"run_{now}" / "error.json"
        err_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.write_text(json.dumps({"error": str(exc), "type": type(exc).__name__, "dry_run": dry_run, "source": "cycle", "observed_at": now}, indent=2) + "\n")
        if not dry_run:
            raise ExperimentError(f"research cycle failed (live, no fixture): {exc}") from exc
        raise
    snap = capture_launch_snapshots("fixture_mint_1111111111111111111111111111111111" if dry_run else "live_placeholder", t0=now, settings=s)
    reviews: dict[str, dict] = {}
    redteams: dict[str, dict] = {}
    for h in hypos:
        oos = results[h["name"]].get("historical")
        rob = results[h["name"]].get("robustness")
        reviews[h["name"]] = review_hypothesis(h, snapshots=snap, oos_report=oos)
        redteams[h["name"]] = redteam_hypothesis(h, oos_report=oos, robustness={"robustness": rob.get("robustness")} if rob else {})
    for name, rep in reviews.items():
        p = ROOT / "data" / "research" / "reviews" / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n")
    for name, rep in redteams.items():
        p = ROOT / "data" / "research" / "redteam" / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n")
    run_id = f"run_{now}"
    run_dir = ROOT / "data" / "research" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, res in results.items():
        payload = {
            "hypothesis": name,
            "historical": res["historical"],
            "robustness": res["robustness"],
            "review": reviews[name],
            "redteam": redteams[name],
            "generated_at": now,
            "source": "cycle",
            "observed_at": now,
        }
        (run_dir / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    rows = build_leaderboard(results)
    lb_path = write_leaderboard(rows)
    bench = run_benchmark(settings=s, dry_run=dry_run)
    bench_path = write_benchmark(bench)
    verdicts = {}
    for h in hypos:
        name = h["name"]
        rt = redteams[name]
        rv = reviews[name]
        oos = results[name]["historical"]
        details = oos.get("details") or {}
        priced = int(details.get("priced", oos.get("oos_signals", 0)))
        coverage = float(details.get("coverage", 1.0))
        if not rv["passed"]:
            verdicts[name] = "FALSIFIED"
        elif rt["verdict"] == "KILLED":
            verdicts[name] = "FALSIFIED"
        elif priced < 10 or coverage < 0.8:
            verdicts[name] = "INSUFFICIENT_DATA"
        elif oos.get("oos_signals", 0) >= 10 and oos.get("best_net_tpsl_sol", 0) > 0:
            verdicts[name] = "PROMISING"
        else:
            verdicts[name] = "INSUFFICIENT_DATA"
    mem["last_run"] = {
        "at": now,
        "dry_run": dry_run,
        "hypotheses": [h["name"] for h in hypos],
        "verdicts": verdicts,
        "source": "cycle",
        "observed_at": now,
    }
    mem["hypotheses"] = list({*mem.get("hypotheses", []), *[h["name"] for h in hypos]})
    save_memory(mem)
    manifest = {
        "run_id": run_id,
        "generated_at": now,
        "dry_run": dry_run,
        "hypotheses": [h["name"] for h in hypos],
        "verdicts": verdicts,
        "leaderboard": str(lb_path),
        "benchmark": str(bench_path),
        "source": "cycle",
        "observed_at": now,
    }
    (ROOT / "data" / "research" / "cycle_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest
