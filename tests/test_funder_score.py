from smartalpha.funder_score import (
    FunderGrade,
    enrich_funder_scores,
    filter_funders_by_grade,
    grade_rank,
    mint_gains_from_candidates,
    score_funder_mints,
)


def test_discovery_quality_grades_medium_or_strong():
    q = score_funder_mints(
        ["a", "b", "c"],
        mint_gains={"a": 500.0, "b": 1200.0, "c": 80.0},
        fetch_live=False,
        sleep=0.0,
    )
    assert q["score_source"] == "discovery"
    assert q["win_rate"] >= 0.5
    assert q["grade"] in ("medium", "strong")
    assert "median_return_pct" in q
    assert q["median_h24_pct"] == q["median_return_pct"]  # backward compat key


def test_filter_funders_by_grade_rank():
    rows = [
        {"address": "good", "quality": {"grade": "strong"}},
        {"address": "mid", "quality": {"grade": "medium"}},
        {"address": "bad", "quality": {"grade": "watch"}},
        {"address": "skip", "quality": {"grade": "skip"}},
    ]
    kept = filter_funders_by_grade(rows, min_grade=FunderGrade.MEDIUM)
    assert {r["address"] for r in kept} == {"good", "mid"}
    assert grade_rank("strong") > grade_rank("medium") > grade_rank("watch")


def test_mint_gains_from_candidates():
    gains = mint_gains_from_candidates(
        [{"mint": "m1", "gain_h24_pct": 300}, {"mint": "m2", "gain_h24_pct": None}]
    )
    assert gains == {"m1": 300.0}


def test_enrich_sets_weight_and_quality():
    out = enrich_funder_scores(
        [{"address": "F1", "weight": 1.0, "mints": ["x", "y"]}],
        mint_gains={"x": 400.0, "y": 500.0},
        fetch_live=False,
        sleep=0.0,
    )
    assert out[0]["quality"]["grade"] in ("medium", "strong")
    assert out[0]["weight"] >= 1.0
