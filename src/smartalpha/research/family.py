"""Family — group hypotheses by feature set for multiple-testing control."""
from __future__ import annotations

import hashlib
import json

from smartalpha.research.canonical import canonical_hash


def family_id(hypo: dict) -> str:
    # family is sorted feature names, threshold-insensitive? For V3, family is feature set
    feats = sorted(hypo.get("features", []))
    # ignore thresholds, just feature set
    blob = json.dumps({"features": feats}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def is_duplicate(hypo: dict, seen_hashes: set[str]) -> bool:
    h = canonical_hash(hypo)
    return h in seen_hashes
