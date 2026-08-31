import json
from pathlib import Path
from types import SimpleNamespace

from smartalpha.config import Settings
from smartalpha.db import Store
from smartalpha.execution import ExecutionEngine
from smartalpha.launch_intel import BuyerProfile, LaunchIntel
from smartalpha.paper_log import PaperSignalInput, log_paper_signal
from smartalpha.prove import run_prove


def _settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.db_path = tmp_path / "smartalpha.db"
    s.signal_min_liquidity_usd = 3000.0
    s.signal_min_unique_buyers = 8
    s.signal_min_velocity = 0.5
    s.signal_min_buy_sell_ratio = 1.5
    s.execution_max_slippage_pct = 5.0
    s.execution_trade_size_sol = 0.5
    return s


def _strict_signal():
    return SimpleNamespace(
        mint="mint",
        creator="creator",
        ts=100,
        strict_entry=True,
        copytrap_risk="low",
        price_usd=1.0,
        liquidity_usd=5000.0,
    )


def test_execution_is_idempotent_and_paper_only_by_default(tmp_path: Path):
    s = _settings(tmp_path)
    s.execution_mode = "paper"
    engine = ExecutionEngine(settings=s, store=Store(s.db_path))

    first = engine.submit_entry(_strict_signal())
    second = engine.submit_entry(_strict_signal())

    assert first.status == second.status == "paper"
    assert len(engine.store.list_execution_orders()) == 1
    assert engine.store.list_open_positions() == []


def test_shadow_quote_replaces_proxy_t0_for_paper(tmp_path: Path):
    s = _settings(tmp_path)
    s.execution_mode = "shadow"
    s.execution_trade_size_sol = 0.1
    store = Store(s.db_path)
    store.upsert_paper_signal(
        mint="mint",
        signal_ts=100,
        creator="creator",
        signature="signature",
        recommendation="dataset",
        copytrap_risk="low",
        liquidity_usd=5000.0,
        strict_signal=True,
        price_usd=1.0,
        snapshots={"0": {"price_usd": 1.0, "source": "dexscreener", "observed_at": 100}},
    )

    class Signer:
        def quote(self, _intent):
            return {
                "output_sol": 0.1,
                "price_usd": 1.1,
                "liquidity_usd": 5000.0,
                "observed_at": 100,
            }

    result = ExecutionEngine(settings=s, store=store, signer=Signer()).submit_entry(_strict_signal())
    row = store.get_paper_signal("mint")

    assert result.status == "quoted"
    assert json.loads(row["snapshots_json"])["0"]["source"] == "signer_quote"
    assert row["price_usd"] == 1.1


def test_canary_requires_proof_before_funds(tmp_path: Path):
    s = _settings(tmp_path)
    s.execution_mode = "canary"
    s.execution_canary_armed = True
    s.execution_trade_size_sol = 0.1
    s.execution_max_daily_loss_sol = 0.3

    class Signer:
        def execute(self, _intent):
            raise AssertionError("should not reach signer")

    result = ExecutionEngine(settings=s, store=Store(s.db_path), signer=Signer()).submit_entry(_strict_signal())

    assert result.status == "rejected"
    assert "PROVEN" in result.reason


def test_canary_requires_arm_and_records_filled_position(tmp_path: Path):
    s = _settings(tmp_path)
    s.execution_mode = "canary"
    s.execution_canary_armed = True
    s.execution_trade_size_sol = 0.1
    s.execution_max_daily_loss_sol = 0.3
    s.execution_require_proven = False

    class Signer:
        def execute(self, _intent):
            return {
                "status": "confirmed",
                "tx_signature": "sig",
                "filled_base_amount": 1000,
                "spent_quote_sol": 0.1,
                "entry_price_usd": 0.001,
            }

        def quote(self, _intent):
            return {"output_sol": 0.1, "observed_at": 100}

    engine = ExecutionEngine(settings=s, store=Store(s.db_path), signer=Signer())
    result = engine.submit_entry(_strict_signal())

    assert result.status == "confirmed"
    assert result.position_opened
    assert engine.store.get_position("mint")["token_amount"] == 1000


def test_paper_uses_the_same_velocity_gate(monkeypatch, tmp_path: Path):
    s = _settings(tmp_path)
    monkeypatch.setattr(
        "smartalpha.paper_log.get_pair_meta",
        lambda _mint: {"price_usd": 1.0, "liquidity_usd": 5000.0, "source": "test", "observed_at": 100},
    )
    intel = LaunchIntel(
        mint="mint",
        buyers=[BuyerProfile(f"w{i}", 1.0, i, f"s{i}") for i in range(8)],
        bundler_wallets=[],
        copytrap_risk="low",
        recommendation="dataset",
        buy_count=8,
        sell_count=4,
        top_buyer_share=0.1,
    )
    inp = PaperSignalInput(
        mint="mint",
        signal_ts=100,
        creator="creator",
        signature="signature",
        recommendation="dataset",
        copytrap_risk="low",
        intel=intel,
        liquidity_usd=5000.0,
        notes=[],
        volume_usd=1000.0,
    )

    log_paper_signal(inp, settings=s, store=Store(s.db_path))

    assert Store(s.db_path).get_paper_signal("mint")["strict_signal"] == 0


def test_prove_applies_entry_gate_and_exit(tmp_path: Path):
    s = _settings(tmp_path)
    records = []
    for i in range(10):
        records.append(
            {
                "mint": f"mint-{i}",
                "pair_address": f"pair-{i}",
                "copytrap_risk": "low",
                "unique_buyers": 8,
                "buy_count": 8,
                "sell_count": 4,
                "top_buyer_share": 0.125,
                "liquidity_usd": 5000,
                "volume_m5_usd": 3000,
                "signal_ts": 100 + i,
                "features_observed_at": 100 + i,
                "gain_h1_pct": 100,
                "gain_h6_pct": 100,
                "gain_h24_pct": 100,
                "split": "oos",
            }
        )
    dataset = tmp_path / "oos.json"
    dataset.write_text(json.dumps({"metadata": {"split": "oos"}, "candidates": records}))

    report = run_prove(dataset, settings=s)

    assert report.phase1_historical.metrics["strict_signals"] == 10
    assert report.phase1_historical.all_passed
    assert report.verdict == "PROMISING"
