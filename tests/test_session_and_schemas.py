import json
from pathlib import Path

from smartalpha.session_funders import (
    build_hot_funders_from_recommended,
    load_session_hot_funders_from_disk,
)


def test_build_hot_funders_respects_grade_and_cex(tmp_path: Path, monkeypatch):
    # known CEX sample from discover_funders
    cex = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    hot, notes = build_hot_funders_from_recommended(
        [
            {
                "address": "GoodFunder1111111111111111111111111111111",
                "label": "cross-2",
                "weight": 1.5,
                "quality": {"grade": "strong"},
            },
            {
                "address": cex,
                "label": "cex",
                "weight": 1.0,
                "quality": {"grade": "strong"},
            },
            {
                "address": "WatchOnly11111111111111111111111111111111",
                "label": "w",
                "weight": 1.0,
                "quality": {"grade": "watch"},
            },
        ],
        enrich=False,
        min_grade="medium",
    )
    assert len(hot) == 1
    assert "GoodFunder" in next(iter(hot))
    assert any("session funders" in n for n in notes)


def test_load_session_from_disk(tmp_path: Path, monkeypatch):
    report = tmp_path / "auto_discover.json"
    report.write_text(
        json.dumps(
            {
                "candidates": [{"mint": "m1", "gain_h24_pct": 500}],
                "recommended_funders": [
                    {
                        "address": "DiskFunder1111111111111111111111111111111",
                        "label": "cross-2",
                        "weight": 1.2,
                        "mints": ["m1", "m2"],
                        "quality": {"grade": "medium"},
                    }
                ],
            }
        )
    )
    from smartalpha import session_funders as sf

    monkeypatch.setattr(sf, "session_report_path", lambda: report)
    hot, notes, path = load_session_hot_funders_from_disk(min_grade="medium")
    assert path == report
    assert len(hot) == 1
    assert any("loaded disk" in n for n in notes)
