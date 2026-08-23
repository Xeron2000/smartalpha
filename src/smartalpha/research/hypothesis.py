"""Hypothesis schema + generator."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from smartalpha.config import ROOT

REQUIRED_FIELDS = ("name", "thesis", "features", "entry_rule", "exit_rule", "expected_edge", "falsification_condition", "known_biases")

TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "funder_repeat_hot_2_organic",
        "thesis": "Funders that funded >=2 prior runners produce hot organic buyers within 90s that predict +300s EV after slippage",
        "features": ["hot_organic_buyers@90s", "liquidity_usd@t0", "copytrap_risk@90s", "funder_grade@train"],
        "entry_rule": "hot_organic_buyers>=2 and copytrap_risk!=high and liquidity>=5000 (allow stale>48h ignore)",
        "exit_rule": "scale_half@100% + runner trail 30% drawdown, max 30m",
        "expected_edge": "OOS net expectancy >0 at 300s net of 15% slippage",
        "falsification_condition": "OOS n>=10 and best_net_tpsl<=0 OR win_rate<35% and net<=1 SOL",
        "known_biases": ["survivorship (discovery from pumped set)", "stale liq", "Dex proxy vs Kline"],
    },
    {
        "name": "early_holder_concentration_low",
        "thesis": "Low early holder concentration (<40% top10) at +30s predicts less dump and higher 900s EV",
        "features": ["holder_concentration@30s", "smart_money_holders@30s", "liquidity_usd@t0"],
        "entry_rule": "top10_holder_share<0.4 and hot_organic>=1 and copytrap!=high",
        "exit_rule": "fixed tp 100% sl 30%",
        "expected_edge": "OOS 900s win_rate>40% net>0",
        "falsification_condition": "OOS n>=10 and 900s EV<=0",
        "known_biases": ["holder snapshot timing", "GMGN label drift"],
    },
    {
        "name": "funder_wallet_age_fresh",
        "thesis": "Fresh wallets (<2h) funded by strong funder are more predictive than reused wallets",
        "features": ["wallet_age_hours@30s", "funder_known@30s", "funder_grade@train"],
        "entry_rule": "fresh_wallets>=2 funded by grade>=medium",
        "exit_rule": "dynamic trail 50% activate 30% drawdown",
        "expected_edge": "OOS net>0 and MFE>MAE",
        "falsification_condition": "OOS net<=0 after slippage",
        "known_biases": ["wallet age pagination capped", "RPC rate limit"],
    },
]


def validate_hypothesis(h: dict) -> tuple[bool, str]:
    for f in REQUIRED_FIELDS:
        if f not in h or h[f] is None or (isinstance(h[f], (list, str)) and len(h[f]) == 0):
            return False, f"missing {f}"
        if f == "features" and not isinstance(h[f], list):
            return False, "features must be list"
    for feat in h.get("features", []):
        if "@" not in feat and "t0" not in feat and "90s" not in feat:
            return False, f"feature {feat} missing timestamp"
    return True, "ok"


def generate_hypotheses(memory: dict | None = None, limit: int = 3) -> list[dict]:
    from smartalpha.research.memory import is_falsified

    out: list[dict] = []
    # First, try DSL examples via compiler (V3)
    try:
        from smartalpha.research.dsl_compiler import compile_file, list_examples
        for p in list_examples():
            if len(out) >= limit:
                break
            try:
                hypo = compile_file(p)
                # map DSL hypo to legacy required fields for downstream
                h = dict(hypo)
                h.setdefault("thesis", h.get("description", "dsl"))
                h.setdefault("expected_edge", "OOS EV >0")
                h.setdefault("known_biases", [])
                h.setdefault("exit_rule", hypo.get("exit_rule", ""))
                h["generated_at"] = int(time.time())
                h["source"] = "dsl"
                h["observed_at"] = int(time.time())
                out.append(h)
            except Exception:
                continue
    except Exception:
        pass
    for tmpl in TEMPLATES:
        if len(out) >= limit:
            break
        if is_falsified(tmpl["name"], memory):
            continue
        # avoid duplicate names from DSL
        if any(o["name"] == tmpl["name"] for o in out):
            continue
        h = dict(tmpl)
        h["generated_at"] = int(time.time())
        h["source"] = "hypothesis"
        h["observed_at"] = int(time.time())
        ok, msg = validate_hypothesis(h)
        if not ok:
            continue
        out.append(h)
        if len(out) >= limit:
            break
    # V3 generator enhancement: if still under limit, generate novel from ledger
    if len(out) < limit:
        try:
            from smartalpha.research.generator import generate_novel_hypotheses
            novel = generate_novel_hypotheses(limit=limit - len(out))
            for n in novel:
                if not any(o["name"] == n["name"] for o in out):
                    out.append(n)
                if len(out) >= limit:
                    break
        except Exception:
            pass
    return out


def write_hypotheses(hypos: list[dict], run_dir: Path | None = None) -> Path:
    base = run_dir or ROOT / "data" / "research" / "hypotheses"
    base.mkdir(parents=True, exist_ok=True)
    for h in hypos:
        p = base / f"{h['name']}.json"
        p.write_text(json.dumps(h, indent=2, ensure_ascii=False) + "\n")
    manifest = base / "_manifest.json"
    manifest.write_text(json.dumps({"generated_at": int(time.time()), "count": len(hypos), "hypos": [h["name"] for h in hypos], "source": "hypothesis", "observed_at": int(time.time())}, indent=2) + "\n")
    return base
