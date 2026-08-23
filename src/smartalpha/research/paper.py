"""Paper — freeze PROMISING hypotheses for prospective validation."""
from __future__ import annotations

import json
import time
from pathlib import Path

from smartalpha.config import ROOT


def paper_path() -> Path:
    return ROOT / "data" / "research" / "paper_candidates.jsonl"


def freeze_paper_candidate(hypo: dict, lineage: dict) -> Path:
    p = paper_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "hypothesis_id": hypo.get("name"),
        "hypothesis": hypo,
        "lineage": lineage,
        "frozen_at": int(time.time()),
        "rule_hash": hypo.get("name"),
        "source": "paper",
    }
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def list_paper_candidates() -> list[dict]:
    p = paper_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
