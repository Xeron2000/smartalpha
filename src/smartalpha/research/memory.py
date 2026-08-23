"""Research Memory — persistent store of falsified & proven hypotheses."""
from __future__ import annotations

import json
import time
from pathlib import Path

from smartalpha.config import ROOT


def memory_path() -> Path:
    return ROOT / "data" / "research" / "memory.json"


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
