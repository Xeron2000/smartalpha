"""DSL Compiler — validate hypothesis DSL and compile to Experiment."""
from __future__ import annotations

import json
from pathlib import Path

from smartalpha.config import ROOT

SCHEMA_PATH = Path(__file__).with_name("hypothesis_dsl.json")


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def validate_hypothesis(data: dict) -> tuple[bool, str]:
    schema = load_schema()
    # minimal validation without external jsonschema dep
    for field in schema.get("required", []):
        if field not in data:
            return False, f"missing required field: {field}"
    # name pattern
    import re
    if not re.match(r"^[a-z0-9_]+$", data.get("name", "")):
        return False, "invalid name pattern"
    if len(data.get("description", "")) < 10:
        return False, "description too short"
    if not isinstance(data.get("features"), list) or not data["features"]:
        return False, "features must be non-empty list"
    if not data.get("entry_rule"):
        return False, "entry_rule required"
    if not data.get("falsification_condition"):
        return False, "falsification_condition required"
    # additionalProperties check
    allowed = set(schema["properties"].keys())
    for k in data:
        if k not in allowed:
            return False, f"unknown field: {k}"
    return True, "ok"


def compile_hypothesis(data: dict) -> dict:
    ok, msg = validate_hypothesis(data)
    if not ok:
        raise ValueError(f"DSL validation failed: {msg}")
    # deterministic compile: map DSL to experiment hypo dict
    hypo = {
        "name": data["name"],
        "description": data["description"],
        "features": data["features"],
        "entry_rule": data["entry_rule"],
        "falsification_condition": data["falsification_condition"],
        "exit_rule": data.get("exit_rule", ""),
        "filters": data.get("filters", {}),
        "source": "dsl_compiler",
    }
    return hypo


def compile_file(path: Path | str) -> dict:
    data = json.loads(Path(path).read_text())
    return compile_hypothesis(data)


def list_examples() -> list[Path]:
    ex_dir = ROOT / "examples" / "hypotheses"
    if not ex_dir.exists():
        ex_dir = Path(__file__).parent.parent.parent / "examples" / "hypotheses"
    return sorted(ex_dir.glob("*.json"))
