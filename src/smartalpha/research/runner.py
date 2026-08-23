"""Runner — Historical → OOS → Robustness with Kline price + slippage."""
from __future__ import annotations

import time
from dataclasses import dataclass

from smartalpha.config import Settings


@dataclass
class ExperimentConfig:
    """True param grid for robustness re-execution (ponytail: 6 explicit configs)."""

    train_ratio: float = 0.70  # base 0.70, window 0.60
    slippage: float = 0.15  # base 15%, perturbs 10%/20%
    threshold: str = "base"  # base hot2, low hot1, high hot3

    # 6 configs: base + 5 perturbs (slippage 10/20, window 0.60, threshold low/high)
    @staticmethod
    def all_configs() -> list[tuple[str, ExperimentConfig]]:
        return [
            ("base", ExperimentConfig(0.70, 0.15, "base")),
            ("slippage_10pct", ExperimentConfig(0.70, 0.10, "base")),
            ("slippage_20pct", ExperimentConfig(0.70, 0.20, "base")),
            ("window_shift", ExperimentConfig(0.60, 0.15, "base")),
            ("threshold_low", ExperimentConfig(0.70, 0.15, "low")),
            ("threshold_high", ExperimentConfig(0.70, 0.15, "high")),
        ]


def _true_robustness(hypo: dict, base_report: dict, settings: Settings | None, dry_run: bool) -> dict:
    """Re-execute strategy under perturbations; not a coefficient multiply."""
    base_net = float(base_report.get("best_net_tpsl_sol", 0))
    results: dict[str, float | bool] = {}
    # 6-config true re-execution via ExperimentConfig (base + 5 perturbs)
    for name, cfg in ExperimentConfig.all_configs():
        if name == "base":
            continue  # base already in base_report
        try:
            s = Settings()
            s.backtest_slippage = cfg.slippage
            train_ratio = cfg.train_ratio
            hypo_variant = dict(hypo)
            if cfg.threshold != "base":
                hypo_variant["_threshold"] = cfg.threshold
            # re-execute via experiment with varied settings
            from smartalpha.research.experiments import (
                _run_walk_forward_with_filter,
                get_experiment,
            )

            exp = get_experiment(hypo.get("name", ""))
            if cfg.threshold != "base" or name == "window_shift":
                rep = _run_walk_forward_with_filter(hypo_variant, settings=s, experiment=exp, train_ratio=train_ratio, dry_run=dry_run)
            else:
                rep = exp.run(hypo_variant, settings=s, dry_run=dry_run)
            results[name + "_net"] = round(rep.best_net_tpsl_sol, 4)
        except Exception as exc:
            results[name + "_net"] = 0.0
            results[name + "_error"] = str(exc)  # type: ignore[assignment]
    # stability: all nets >0 and not collapsing
    nets = [v for k, v in results.items() if k.endswith("_net") and isinstance(v, (int, float))]
    stable = base_net > 0 and all(n > 0 for n in nets) and min(nets, default=0) > base_net * 0.5 if nets else False
    return {
        **{k: v for k, v in results.items()},
        "stable": stable,
        "source": "runner",
        "observed_at": int(time.time()),
        "rerun": True,  # marker that this was true re-execution, not mock coefficient
    }


class ExperimentError(RuntimeError):
    """Live experiment failed — no fixture fallback allowed."""


def run_historical(hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> dict:
    from smartalpha.research.experiments import get_experiment

    exp = get_experiment(hypo.get("name", ""))
    res = exp.run(hypo, settings=settings, dry_run=dry_run)
    # Convert ExperimentResult dataclass to dict for downstream
    return {
        "hypothesis": res.hypothesis,
        "oos_signals": res.oos_signals,
        "best_net_tpsl_sol": res.best_net_tpsl_sol,
        "best_win_rate": res.best_win_rate,
        "train_funders": res.train_funders,
        "test_mints": res.test_mints,
        "source": res.source,
        "observed_at": res.observed_at,
        "walk_forward": res.source != "fixture",
        "details": res.details,
    }


def run_oos(hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> dict:
    return run_historical(hypo, settings=settings, dry_run=dry_run)


def run_robustness(hypo: dict, oos_report: dict | None = None, settings: Settings | None = None, dry_run: bool = False) -> dict:
    base_report = oos_report or {"best_net_tpsl_sol": 0.42}
    rob = _true_robustness(hypo, base_report, settings, dry_run)
    return {
        "hypothesis": hypo["name"],
        "base_net": float(base_report.get("best_net_tpsl_sol", 0)),
        "robustness": rob,
        "passed": bool(rob.get("stable", False)),
        "source": "runner",
        "observed_at": int(time.time()),
    }


def run_all(hypos: list[dict], settings: Settings | None = None, dry_run: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for h in hypos:
        try:
            oos = run_oos(h, settings=settings, dry_run=dry_run)
        except Exception as exc:
            msg = str(exc)
            if "HISTORICAL_UNAVAILABLE" in msg or "HISTORICAL_INCOMPLETE" in msg or "MISSING_FEATURE" in msg or "missing" in msg.lower():
                # per-hypothesis insufficient, not whole cycle crash
                oos = {
                    "hypothesis": h.get("name"),
                    "oos_signals": 0,
                    "best_net_tpsl_sol": 0.0,
                    "best_win_rate": 0.0,
                    "train_funders": 0,
                    "test_mints": 0,
                    "source": "insufficient",
                    "observed_at": int(time.time()),
                    "walk_forward": False,
                    "details": {"selected_mints": [], "priced": 0, "executed": 0, "coverage": 0.0, "wins": 0, "losses": 0, "experiment": h.get("name"), "kline_engine": "30s", "mfe": 0.0, "mae": 0.0, "maxDD": 0.0, "status": "HISTORICAL_UNAVAILABLE", "error": msg},
                }
            else:
                raise
        try:
            rob = run_robustness(h, oos, settings=settings, dry_run=dry_run)
        except Exception:
            rob = {"stable": False, "source": "runner", "observed_at": int(time.time()), "error": "robustness_failed"}
        out[h["name"]] = {"historical": oos, "robustness": rob, "source": "runner", "observed_at": int(time.time())}
    return out
