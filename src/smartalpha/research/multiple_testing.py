"""Multiple Testing — simple FDR gate for V3."""
from __future__ import annotations


def bh_fdr(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    # Benjamini-Hochberg: returns which are significant
    if not p_values:
        return []
    m = len(p_values)
    sorted_idx = sorted(range(m), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in sorted_idx]
    thresh = 0
    for i, p in enumerate(sorted_p):
        if p <= (i + 1) / m * alpha:
            thresh = i
    sig = [False] * m
    for i in range(thresh + 1):
        sig[sorted_idx[i]] = True
    return sig


def family_gate(trials_in_family: int, max_variants: int = 5) -> bool:
    # simple gate: if family has too many variants, require stronger evidence
    return trials_in_family <= max_variants
