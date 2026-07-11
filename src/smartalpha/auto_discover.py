from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from smartalpha.config import ROOT, Settings, rpc_url
from smartalpha.discover_funders import discover_funders, load_mint_list
from smartalpha.funder_score import enrich_funder_scores, mint_gains_from_candidates
from smartalpha.mint_sources import CandidateMint, find_candidate_mints
from smartalpha.rpc import SolanaRpc


@dataclass
class AutoDiscoverReport:
    candidates: list[CandidateMint]
    source_notes: list[str]
    mints_traced: list[str]
    discover: object  # DiscoverReport
    skipped_discovery: bool = False
    notes: list[str] = field(default_factory=list)


def run_auto_discover(
    settings: Settings | None = None,
    *,
    min_gain_pct: float = 300.0,
    mint_limit: int = 15,
    min_mint_hits: int = 2,
    max_buyers: int = 15,
    extra_mints_file: Path | None = None,
) -> AutoDiscoverReport:
    settings = settings or Settings()
    notes: list[str] = []

    candidates, source_notes = find_candidate_mints(
        settings, min_gain_pct=min_gain_pct, limit=mint_limit
    )
    mints = [c.mint for c in candidates]

    if extra_mints_file and extra_mints_file.exists():
        extra = load_mint_list(extra_mints_file)
        for m in extra:
            if m not in mints:
                mints.append(m)
                candidates.append(CandidateMint(m, "manual_file"))
        notes.append(f"merged {len(extra)} from {extra_mints_file.name}")

    if not mints:
        notes.append("no candidate mints - lower --min-gain or set GMGN_COOKIE / add mints.txt")
        from smartalpha.discover_funders import DiscoverReport

        empty = DiscoverReport(0, [], [], ["no mints"])
        return AutoDiscoverReport([], source_notes, [], empty, skipped_discovery=True, notes=notes)

    rpc = SolanaRpc(rpc_url(settings))

    report = discover_funders(
        mints,
        rpc,
        max_buyers=max_buyers,
        min_mint_hits=min_mint_hits,
        settings=settings,
    )

    # Attach quality using discovery gains (not live rolling h24)
    gains = mint_gains_from_candidates(candidates)
    if report.recommended:
        report.recommended = enrich_funder_scores(
            report.recommended,
            mint_gains=gains,
            sleep=0.0,
            fetch_live=False,
        )
        notes.append(
            f"funder quality: discovery gains on {len(gains)} mints, "
            f"{sum(1 for r in report.recommended if (r.get('quality') or {}).get('grade') in ('medium','strong'))}"
            f"/{len(report.recommended)} grade≥medium"
        )

    return AutoDiscoverReport(
        candidates=candidates,
        source_notes=source_notes,
        mints_traced=mints,
        discover=report,
        notes=notes,
    )


def write_auto_discover_report(report: AutoDiscoverReport, path: Path | None = None) -> Path:
    p = path or ROOT / "data" / "auto_discover.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    dr = report.discover
    payload = {
        "candidates": [
            {
                "mint": c.mint,
                "source": c.source,
                "gain_h24_pct": c.gain_h24_pct,
                "pair": c.pair,
                "dex": c.dex,
                "url": c.url,
            }
            for c in report.candidates
        ],
        "source_notes": report.source_notes,
        "mints_traced": report.mints_traced,
        "recommended_funders": getattr(dr, "recommended", []),
        "skipped_mints": getattr(dr, "skipped_mints", []),
        "notes": report.notes,
        "skipped_discovery": report.skipped_discovery,
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return p
