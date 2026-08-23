"""Canonicalization & Deduplication for hypotheses."""
from __future__ import annotations

import hashlib
import json


def canonical_hash(hypo: dict) -> str:
    # canonicalize: sort features, normalize entry_rule spacing, sort keys
    data = {
        "name": hypo.get("name"),
        "features": sorted(hypo.get("features", [])),
        "entry_rule": " ".join(hypo.get("entry_rule", "").split()),
        "universe": hypo.get("universe", "solana"),
    }
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
