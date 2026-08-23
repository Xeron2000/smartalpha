"""Research Memory — persistent store of falsified & proven hypotheses."""
from __future__ import annotations

import json
import time
from pathlib import Path

from smartalpha.config import ROOT


def memory_path() -> Path:
    return ROOT / "data" / "research" / "memory.json"


def ledger_path() -> Path:
    return ROOT / "data" / "research" / "memory_ledger.jsonl"


def load_memory() -> dict:
    p = memory_path()
    if not p.exists():
        return {"hypotheses": [], "falsified": [], "proven": [], "updated_at": int(time.time()), "source": "memory", "observed_at": int(time.time())}
    try:
        data = json.loads(p.read_text())
        if "hypotheses" not in data:
            data["hypotheses"] = []
        return data
    except Exception:
        return {"hypotheses": [], "falsified": [], "proven": [], "updated_at": int(time.time()), "source": "memory", "observed_at": int(time.time())}


def save_memory(mem: dict) -> Path:
    p = memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    mem["updated_at"] = int(time.time())
    mem["observed_at"] = int(time.time())
    mem["source"] = "memory"
    p.write_text(json.dumps(mem, indent=2, ensure_ascii=False) + "\n")
    return p


def is_falsified(name: str, mem: dict | None = None) -> bool:
    m = mem or load_memory()
    for h in m.get("falsified", []):
        if h.get("name") == name:
            return True
    return False


def record_falsified(hypo: dict, reason: str) -> None:
    mem = load_memory()
    mem.setdefault("falsified", []).append({"name": hypo.get("name"), "reason": reason, "at": int(time.time()), "source": "redteam", "observed_at": int(time.time())})
    save_memory(mem)


def record_proven(hypo: dict) -> None:
    mem = load_memory()
    mem.setdefault("proven", []).append({"name": hypo.get("name"), "at": int(time.time()), "source": "proven", "observed_at": int(time.time())})
    save_memory(mem)


def append_ledger(entry: dict) -> Path:
    """Append-only ledger for hypothesis_id / DSL / lineage / OOS / verdict."""
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # ensure hypothesis_id and verdict present
    rec = dict(entry)
    rec.setdefault("hypothesis_id", rec.get("hypothesis") or rec.get("name") or f"hypo_{int(time.time())}")
    rec.setdefault("observed_at", int(time.time()))
    rec["source"] = "memory_ledger"
    # dedup: if same hypothesis_id already exists, skip append unless verdict differs
    if p.exists():
        try:
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                old = json.loads(line)
                if old.get("hypothesis_id") == rec["hypothesis_id"] and old.get("verdict") == rec.get("verdict"):
                    return p
        except Exception:
            pass
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def read_ledger() -> list[dict]:
    p = ledger_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def query_ledger(verdict: str | None = None) -> list[dict]:
    rows = read_ledger()
    if verdict is None:
        return rows
    return [r for r in rows if r.get("verdict") == verdict]
