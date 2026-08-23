"""Generator — produce novel hypotheses from Memory ledger."""
from __future__ import annotations

import random
import time

from smartalpha.research.dsl_compiler import compile_hypothesis
from smartalpha.research.memory import read_ledger

FEATURE_POOL = [
    "hot_organic_buyers",
    "top10_holder_rate",
    "fresh_wallets",
    "liquidity_usd",
    "bundler_wallets",
    "copytrap_risk",
]

THRESHOLDS = {
    "hot_organic_buyers": [1, 2, 3],
    "top10_holder_rate": [0.3, 0.4, 0.5],
    "fresh_wallets": [1, 2, 3],
    "liquidity_usd": [5000, 10000, 20000],
}


def _seen_names() -> set[str]:
    return {r.get("hypothesis_id") or r.get("hypothesis") for r in read_ledger()}


def _random_feature_combo() -> tuple[list[str], str]:
    # pick 1-2 features with thresholds
    feats = random.sample(FEATURE_POOL, k=random.choice([1, 2]))
    parts = []
    for f in feats:
        if f in THRESHOLDS:
            thr = random.choice(THRESHOLDS[f])
            if f == "top10_holder_rate":
                parts.append(f"{f} < {thr}")
            elif f == "liquidity_usd":
                parts.append(f"{f} > {thr}")
            else:
                parts.append(f"{f} >= {thr}")
        else:
            parts.append(f"{f} == 0" if f == "bundler_wallets" else f"{f} != 'high'")
    entry = " and ".join(parts)
    return feats, entry


def generate_novel_hypotheses(limit: int = 3) -> list[dict]:
    seen = _seen_names()
    out: list[dict] = []
    attempts = 0
    while len(out) < limit and attempts < limit * 10:
        attempts += 1
        feats, entry = _random_feature_combo()
        name = f"auto_{'_'.join(f[:3] for f in feats)}_{int(time.time())%10000}_{attempts}"
        # ensure unique name not in seen
        if name in seen:
            continue
        cand = {
            "name": name.lower().replace("-", "_"),
            "description": f"Auto generated from ledger: {entry}",
            "features": feats,
            "entry_rule": entry,
            "exit_rule": random.choice(["scale_half", "fixed TP 100% SL 30%", "dynamic_trail"]),
            "falsification_condition": "EV <= 0 or coverage < 0.8",
        }
        try:
            hypo = compile_hypothesis(cand)
            # add legacy fields for downstream
            hypo["thesis"] = cand["description"]
            hypo["expected_edge"] = "OOS EV >0"
            hypo["known_biases"] = []
            hypo["generated_at"] = int(time.time())
            hypo["source"] = "generator"
            hypo["observed_at"] = int(time.time())
            out.append(hypo)
            seen.add(hypo["name"])
        except Exception:
            continue
    return out


def generate_from_ledger(limit: int = 3) -> list[dict]:
    """Main entry for V3: if ledger has many FALSIFIED, mutate them."""
    novel = generate_novel_hypotheses(limit=limit)
    if novel:
        return novel
    # fallback to static
    from smartalpha.research.hypothesis import TEMPLATES
    return TEMPLATES[:limit]
