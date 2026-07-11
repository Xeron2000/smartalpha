import json
import time
from pathlib import Path

from smartalpha.db import Store
from smartalpha.paper_log import export_paper_csv, paper_health


def test_seen_mints_and_paper_upsert(tmp_path: Path):
    db = tmp_path / "t.db"
    store = Store(db)
    assert store.try_seen_mint("mintA", "sig1", "creator") is True
    assert store.try_seen_mint("mintA", "sig2", "creator") is False
    store.mark_seen_mint_done("mintA")
    assert store.is_mint_seen("mintA") is True

    store.upsert_paper_signal(
        mint="mintA",
        signal_ts=int(time.time()),
        creator="c",
        signature="s",
        recommendation="follow_cohort",
        copytrap_risk="low",
        hot_organic_buyers=2,
        hot_funders=["f1"],
        liquidity_usd=6000.0,
        strict_signal=True,
        price_usd=1e-6,
        snapshots={
            "0": {"price_usd": 1e-6},
            "90": {"price_usd": 1.2e-6},
        },
        notes="t",
    )
    rows = store.list_paper_signals(limit=10)
    assert len(rows) == 1
    assert rows[0]["strict_signal"] in (1, True)
    assert json.loads(rows[0]["hot_funders_json"]) == ["f1"]

    health = paper_health(store=store)
    assert health["paper_rows"] == 1
    assert health["strict_rows"] == 1
    assert health["with_price_t0"] == 1


def test_export_paper_gain_from_t0(tmp_path: Path):
    from smartalpha.config import Settings

    db = tmp_path / "t.db"
    store = Store(db)
    store.upsert_paper_signal(
        mint="m",
        signal_ts=1,
        creator="",
        signature="",
        recommendation="skip",
        copytrap_risk="low",
        hot_organic_buyers=0,
        hot_funders=[],
        liquidity_usd=None,
        strict_signal=False,
        price_usd=1.0,
        snapshots={"0": {"price_usd": 1.0}, "90": {"price_usd": 1.5}},
        notes="",
    )
    out = tmp_path / "p.csv"
    s = Settings()
    # override db
    s.db_path = db  # type: ignore[misc]
    n = export_paper_csv(out, settings=s, store=store)
    assert n == 1
    text = out.read_text()
    assert "50.0" in text or "50" in text  # +50% from 1.0 -> 1.5
