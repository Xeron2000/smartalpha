"""Executable Experiments — each Hypothesis maps to its own signal generation."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from smartalpha.config import Settings


class MissingFeatureError(RuntimeError):
    """Live missing real feature — must not fallback to synthetic."""


@dataclass
class ExperimentResult:
    hypothesis: str
    oos_signals: int
    best_net_tpsl_sol: float
    best_win_rate: float
    train_funders: int
    test_mints: int
    source: str
    observed_at: int
    details: dict[str, Any] | None = None


class BaseExperiment:
    name: str = "base"

    def select_features(self, mint: str, hypo: dict, settings: Settings | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def should_enter(self, features: dict[str, Any], hypo: dict) -> bool:
        raise NotImplementedError

    def run(self, hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> ExperimentResult:
        raise NotImplementedError


class FunderRepeatExperiment(BaseExperiment):
    name = "funder_repeat_hot_2_organic"

    def select_features(self, mint: str, hypo: dict, settings: Settings | None = None) -> dict[str, Any]:
        dry = bool(hypo.get("_dry_run"))
        if not dry:
            try:
                from smartalpha.config import Settings as _S
                from smartalpha.config import rpc_url
                from smartalpha.launch_intel import analyze_launch
                from smartalpha.rpc import SolanaRpc
                from smartalpha.signal_rules import hot_organic_buyers
                s = settings or _S()
                if s.helius_key and mint.endswith("pump"):
                    rpc = SolanaRpc(rpc_url(s))
                    # Use frozen train funders, not empty
                    train_funders = hypo.get("_train_funders") or set()
                    hot_funders = {addr: True for addr in train_funders} if train_funders else {}
                    from smartalpha.funder import dex_pair_created_at
                    launch_ts = dex_pair_created_at(mint)
                    if launch_ts is None:
                        raise MissingFeatureError(f"missing launch_ts for {mint}")
                    available_at = launch_ts + 90
                    as_of_ts = available_at
                    reconstructed_at = int(time.time())
                    intel = analyze_launch(mint, rpc, settings=s, hot_funders=hot_funders, as_of_ts=as_of_ts, launch_ts=launch_ts)
                    # completeness: window must be fully backfilled, otherwise HISTORICAL_INCOMPLETE
                    if not getattr(intel, "window_complete", True):
                        raise MissingFeatureError(f"HISTORICAL_INCOMPLETE: funder window for {mint} not fully backfilled to launch")
                    hot_organic = len(hot_organic_buyers(intel))
                    repeated = bool(intel.hot_funder_hits)
                    if not intel.buyers:
                        raise MissingFeatureError(f"no buyers for live mint {mint} at as_of {as_of_ts}")
                    return {"hot_organic": hot_organic, "repeated_funder": repeated, "mint": mint, "available_at": available_at, "as_of_ts": as_of_ts, "reconstructed_at": reconstructed_at, "observed_at": reconstructed_at, "evidence_mode": "historical_reconstruction", "source": "funder", "launch_ts": launch_ts, "window_complete": True}
                raise MissingFeatureError(f"missing helius or not pump {mint}")
            except MissingFeatureError:
                raise
            except Exception as exc:
                raise MissingFeatureError(f"missing funder feature for {mint}: {exc}") from exc
        # Dry-run synthetic — only via dry_run=True
        import hashlib
        h = int(hashlib.sha256(mint.encode()).hexdigest(), 16) % 10
        available_at = 90 + (h * 10)
        observed_at = available_at + 5
        hot_organic = (h % 4)
        repeated = (h % 2 == 0)
        return {"hot_organic": hot_organic, "repeated_funder": repeated, "mint": mint, "observed_at": observed_at, "available_at": available_at, "as_of_ts": available_at, "reconstructed_at": int(time.time()), "evidence_mode": "synthetic", "source": "funder"}

    def should_enter(self, features: dict[str, Any], hypo: dict) -> bool:
        thresh = 2
        if hypo.get("_threshold") == "low":
            thresh = 1
        elif hypo.get("_threshold") == "high":
            thresh = 3
        return features.get("hot_organic", 0) >= thresh and bool(features.get("repeated_funder"))

    def run(self, hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> ExperimentResult:
        hypo = dict(hypo)
        hypo["_dry_run"] = dry_run
        return _run_walk_forward_with_filter(hypo, settings, experiment=self, dry_run=dry_run)


class HolderConcentrationExperiment(BaseExperiment):
    name = "early_holder_concentration_low"

    def select_features(self, mint: str, hypo: dict, settings: Settings | None = None) -> dict[str, Any]:
        dry = bool(hypo.get("_dry_run"))
        if not dry:
            try:
                s = settings or Settings()
                if s.gmgn_api_key and mint.endswith("pump"):
                    # Holder is PROSPECTIVE_ONLY: historical without snapshot is HISTORICAL_UNAVAILABLE
                    raise MissingFeatureError(f"HISTORICAL_UNAVAILABLE: holder snapshot for {mint} at +30s not prospectively captured")
                raise MissingFeatureError(f"missing gmgn or not pump {mint}")
            except MissingFeatureError:
                raise
            except Exception as exc:
                raise MissingFeatureError(f"missing holder feature for {mint}: {exc}") from exc
        import hashlib
        h_full = int(hashlib.sha256(mint.encode()).hexdigest(), 16) % 100
        h = h_full % 10
        available_at = 30 + (h * 10)
        observed_at = available_at + 5
        top10 = (h_full % 100) / 100
        hot_organic = (h_full % 3)
        return {"top10_holder_rate": top10, "hot_organic": hot_organic, "mint": mint, "observed_at": observed_at, "available_at": available_at, "as_of_ts": available_at, "reconstructed_at": int(time.time()), "evidence_mode": "synthetic", "source": "holder"}

    def should_enter(self, features: dict[str, Any], hypo: dict) -> bool:
        top_thresh = 0.4
        hot_thresh = 1
        if hypo.get("_threshold") == "low":
            top_thresh = 0.5
            hot_thresh = 1
        elif hypo.get("_threshold") == "high":
            top_thresh = 0.3
            hot_thresh = 2
        return features.get("top10_holder_rate", 1) < top_thresh and features.get("hot_organic", 0) >= hot_thresh

    def run(self, hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> ExperimentResult:
        hypo = dict(hypo)
        hypo["_dry_run"] = dry_run
        return _run_walk_forward_with_filter(hypo, settings, experiment=self, dry_run=dry_run)


class WalletAgeExperiment(BaseExperiment):
    name = "funder_wallet_age_fresh"

    def select_features(self, mint: str, hypo: dict, settings: Settings | None = None) -> dict[str, Any]:
        dry = bool(hypo.get("_dry_run"))
        if not dry:
            try:
                from smartalpha.config import Settings as _S
                from smartalpha.config import rpc_url
                from smartalpha.launch_intel import analyze_launch
                from smartalpha.rpc import SolanaRpc
                s = settings or _S()
                if s.helius_key and mint.endswith("pump"):
                    rpc = SolanaRpc(rpc_url(s))
                    from smartalpha.funder import dex_pair_created_at, wallet_age_hours_at
                    launch_ts = dex_pair_created_at(mint)
                    if launch_ts is None:
                        raise MissingFeatureError(f"missing launch_ts for {mint}")
                    available_at = launch_ts + 30
                    as_of_ts = available_at
                    reconstructed_at = int(time.time())
                    intel = analyze_launch(mint, rpc, settings=s, hot_funders={}, as_of_ts=as_of_ts, launch_ts=launch_ts)
                    if not getattr(intel, "window_complete", True):
                        raise MissingFeatureError(f"HISTORICAL_INCOMPLETE: wallet_age window for {mint} not fully backfilled")
                    fresh = 0
                    for b in intel.buyers[:5]:
                        res = wallet_age_hours_at(rpc, b.wallet, as_of_ts)
                        # new API returns (age, complete) tuple
                        if isinstance(res, tuple):
                            age, complete = res
                        else:
                            age, complete = res, True
                        if not complete:
                            continue
                        if age is not None and age < 2:
                            fresh += 1
                    return {"fresh_wallets": fresh, "mint": mint, "available_at": available_at, "as_of_ts": as_of_ts, "reconstructed_at": reconstructed_at, "observed_at": reconstructed_at, "evidence_mode": "historical_reconstruction", "source": "wallet_age", "launch_ts": launch_ts, "window_complete": True}
                raise MissingFeatureError(f"missing helius or not pump {mint}")
            except MissingFeatureError:
                raise
            except Exception as exc:
                raise MissingFeatureError(f"missing wallet_age feature for {mint}: {exc}") from exc
        import hashlib
        h = int(hashlib.sha256((mint + "fresh").encode()).hexdigest(), 16) % 10
        available_at = 30 + (h * 10)
        observed_at = available_at + 5
        fresh = h % 5
        return {"fresh_wallets": fresh, "mint": mint, "observed_at": observed_at, "available_at": available_at, "as_of_ts": available_at, "reconstructed_at": int(time.time()), "evidence_mode": "synthetic", "source": "wallet_age"}

    def should_enter(self, features: dict[str, Any], hypo: dict) -> bool:
        thresh = 2
        if hypo.get("_threshold") == "low":
            thresh = 1
        elif hypo.get("_threshold") == "high":
            thresh = 3
        return features.get("fresh_wallets", 0) >= thresh

    def run(self, hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> ExperimentResult:
        hypo = dict(hypo)
        hypo["_dry_run"] = dry_run
        return _run_walk_forward_with_filter(hypo, settings, experiment=self, dry_run=dry_run)


def _has_real_data() -> bool:
    from smartalpha.research.universe import auto_discover_fallback_path
    return auto_discover_fallback_path().exists()


def _run_walk_forward_with_filter(hypo: dict, settings: Settings | None, experiment: BaseExperiment, train_ratio: float = 0.7, dry_run: bool = False) -> ExperimentResult:
    from smartalpha.backtest_funders import load_mints_with_pairs
    from smartalpha.funder import kline_candles
    from smartalpha.research.execution import (
        parse_kline_candles,
        simulate_dynamic_trail,
        simulate_fixed,
        simulate_scale_half,
    )
    from smartalpha.research.runner import ExperimentError
    from smartalpha.walk_forward import run_walk_forward
    s = settings or Settings()
    synthetic_mode = False
    seen_mints_loaded: list[tuple[str, int]] | None = None
    # Research OOS universe must come from Helius launch ledger (pre-outcome), not winners
    if not dry_run:
        try:
            from smartalpha.research.universe import load_research_universe
            seen = load_research_universe(settings=s, limit=2000)
            # load_research_universe returns list of (mint, None) if ledger has data
            if seen:
                # need ts for ordering — fetch directly
                from smartalpha.db import Store
                store = Store(s.db_path)
                seen_with_ts = store.list_seen_mints(limit=2000)
                if seen_with_ts:
                    seen_mints_loaded = seen_with_ts
        except Exception:
            pass
    if seen_mints_loaded:
        mints = [(mint, None) for mint, _ in seen_mints_loaded]
        ledger_mint_times = {m: ts for m, ts in seen_mints_loaded}
        # single split from ledger ts — no second split inside walk_forward
        wf = run_walk_forward(mints, settings=s, split_mode="chronological", train_ratio=train_ratio, position_sol=0.5, mint_times=ledger_mint_times)
    else:
        # outcome-blind fallback: use universe helper (auto_discover path hidden there)
        from smartalpha.research.universe import auto_discover_fallback_path
        path = auto_discover_fallback_path()
        if not path.exists():
            if dry_run:
                synthetic_mode = True
                mints = [(f"DryMint{i}1111111111111111111111111111111111pump", None) for i in range(30)]
            else:
                raise ExperimentError(f"missing {path} — live cycle requires real discovery data (seen_mints ledger empty and no auto_discover)")
        else:
            mints = load_mints_with_pairs(path)
        if not mints:
            raise ExperimentError("no mints in discovery file")
    if synthetic_mode:
        n = len(mints)
        cut = max(1, min(n - 1, int(n * train_ratio)))
        train_mints = [m for m, _ in mints[:cut]]
        test_mints = [m for m, _ in mints[cut:]]
        wf_train_funders = [{"address": f"SynFunder{i}"} for i in range(3)]
        class _WF:
            pass
        wf = _WF()
        wf.train_funders = wf_train_funders
        wf.test_mints = test_mints
        wf.test_compare = None
        wf.train_mints = train_mints
    elif seen_mints_loaded is None:
        wf = run_walk_forward(mints, settings=s, split_mode="chronological", train_ratio=train_ratio, position_sol=0.5)
    test_mints = wf.test_mints or []
    # Outcome-blind universe: candidate must be observed before test_window_start, not after outcome
    # For launch ledger (seen_mints) this is already outcome-blind, skip auto_discover check
    if not synthetic_mode and seen_mints_loaded is None:
        try:
            import json as _j
            data = _j.loads(path.read_text())
            cand_map = {}
            for c in data.get("candidates") or []:
                cand_map[c["mint"]] = c.get("candidate_observed_at") or data.get("candidate_observed_at") or data.get("generated_at")
            # also include mints_traced without candidate entry -> assume observed at generated_at
            gen_at = data.get("candidate_observed_at") or data.get("generated_at")
            test_window_start = getattr(wf, "test_window", (0, 0))[0] or 0
            if test_window_start and gen_at:
                # if universe itself was generated after test_window, it's outcome-conditioned
                if gen_at >= test_window_start:
                    # mark incomplete — don't silently use winners
                    raise ValueError(f"HISTORICAL_INCOMPLETE: candidate_observed_at {gen_at} >= test_window_start {test_window_start}")
            filtered = []
            for m in test_mints:
                obs = cand_map.get(m) or gen_at
                if obs is not None and test_window_start and obs >= test_window_start:
                    continue
                filtered.append(m)
            if len(filtered) != len(test_mints):
                # outcome-conditioned mints removed
                test_mints = filtered
                wf.test_mints = filtered
        except ValueError:
            raise
        except Exception:
            pass
    # Freeze train funder set for FunderRepeat (train window only)
    train_funder_set = {f.get("address") or f.get("funder") or str(f) for f in (wf.train_funders or [])}
    # Also handle synthetic wf_train_funders
    hypo_with_train = dict(hypo)
    hypo_with_train["_train_funders"] = train_funder_set
    selected: list[dict] = []
    total_pnl = 0.0
    wins = 0
    losses = 0
    mfe_list: list[float] = []
    mae_list: list[float] = []
    pnl_series: list[float] = []
    for mint in test_mints:
        try:
            feats = experiment.select_features(mint, hypo_with_train, settings=s)
            if not experiment.should_enter(feats, hypo_with_train):
                continue
            evidence_mode = feats.get("evidence_mode") or ("historical_reconstruction" if feats.get("as_of_ts") else "prospective_snapshot")
            available_at = int(feats.get("available_at") or 0)
            as_of_ts = int(feats.get("as_of_ts") or 0)
            observed_at = int(feats.get("observed_at") or 0)
            reconstructed_at = int(feats.get("reconstructed_at") or observed_at)
            if evidence_mode == "historical_reconstruction" and as_of_ts:
                entry_base = max(available_at, as_of_ts)
            elif evidence_mode == "synthetic":
                entry_base = max(available_at, observed_at)
            else:
                entry_base = max(available_at, observed_at)
            entry_ts = entry_base + 5
            signal_ts = entry_ts
            entry_candle_ts = None
            selected.append({"mint": mint, "features": feats, "feature_observed_at": observed_at, "available_at": available_at, "as_of_ts": as_of_ts, "reconstructed_at": reconstructed_at, "evidence_mode": evidence_mode, "signal_ts": signal_ts, "entry_ts": entry_ts, "entry_candle_ts": entry_candle_ts})
            raw = kline_candles(mint, signal_ts=signal_ts)
            if not raw and synthetic_mode:
                import hashlib
                h = int(hashlib.sha256((mint + str(signal_ts)).encode()).hexdigest(), 16)
                base_price = 1.0
                raw = []
                price = base_price
                for i in range(30):
                    bit = (h >> (i % 8)) & 1
                    change = 0.02 if bit else -0.015
                    if experiment.name == "funder_repeat_hot_2_organic":
                        change += 0.015
                    elif experiment.name == "early_holder_concentration_low":
                        change -= 0.005
                    price = max(0.1, price * (1 + change))
                    raw.append({"time": (signal_ts + i * 30) * 1000, "open": str(price), "high": str(price * 1.01), "low": str(price * 0.99), "close": str(price), "volume": "100"})
            if not raw:
                selected[-1]["status"] = "UNPRICED"
                continue
            candles = [c for c in parse_kline_candles(raw) if c.time >= entry_ts]
            if not candles:
                selected[-1]["status"] = "UNPRICED"
                continue
            # Use open of first tradable candle (GMGN time is candle open), not close
            entry = candles[0].open if candles else 0
            selected[-1]["entry_candle_ts"] = candles[0].time if candles else None
            selected[-1]["entry_price"] = entry
            if not entry:
                selected[-1]["status"] = "UNPRICED"
                continue
            if experiment.name == "funder_repeat_hot_2_organic":
                pnl, _ = simulate_scale_half(candles, entry, 0.5, s.backtest_slippage)
            elif experiment.name == "early_holder_concentration_low":
                pnl, _ = simulate_fixed(candles, entry, 0.5, s.backtest_slippage, tp_pct=100, sl_pct=30)
            else:
                pnl, _ = simulate_dynamic_trail(candles, entry, 0.5, s.backtest_slippage)
            if pnl is None:
                selected[-1]["status"] = "UNPRICED"
                continue
            selected[-1]["status"] = "PRICED"
            selected[-1]["pnl"] = pnl
            # per-trade MFE/MAE from candles
            try:
                mfe = max((c.high - entry) / entry * 100 for c in candles)
                mae = min((c.low - entry) / entry * 100 for c in candles)
                mfe_list.append(float(mfe))
                mae_list.append(float(mae))
            except Exception:
                pass
            pnl_series.append(float(pnl))
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1
        except MissingFeatureError as exc:
            msg = str(exc)
            if "HISTORICAL_UNAVAILABLE" in msg or "HISTORICAL_INCOMPLETE" in msg or "missing" in msg.lower():
                # per-mint insufficient, not whole experiment crash
                # record as not selected (skip) to allow other mints to proceed
                continue
            raise
        except Exception:
            if selected and selected[-1].get("mint") == mint:
                selected[-1]["status"] = "UNPRICED"
            continue
    oos_signals = len(selected)
    priced = len([s for s in selected if s.get("status") == "PRICED"])
    executed = wins + losses
    coverage = priced / max(1, oos_signals) if oos_signals else 0.0
    win_rate = wins / max(1, wins + losses) if (wins + losses) else 0.0
    # compute MFE/MAE/maxDD
    mfe_val = round(max(mfe_list), 2) if mfe_list else 0.0
    mae_val = round(min(mae_list), 2) if mae_list else 0.0
    max_dd = 0.0
    if pnl_series:
        cum = 0.0
        peak = 0.0
        dd = 0.0
        for pnl in pnl_series:
            cum += pnl
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        max_dd = round(dd, 4)
    return ExperimentResult(
        hypo["name"],
        oos_signals,
        round(total_pnl, 4),
        round(win_rate, 3),
        len(wf.train_funders),
        len(wf.test_mints),
        "gmgn" if s.gmgn_api_key else "live",
        int(time.time()),
        {"selected_mints": selected, "priced": priced, "executed": executed, "coverage": round(coverage, 3), "wins": wins, "losses": losses, "experiment": experiment.name, "kline_engine": "30s", "mfe": mfe_val, "mae": mae_val, "maxDD": max_dd},
    )

class BundlerAvoidExperiment(BaseExperiment):
    name = "bundler_avoid"

    def select_features(self, mint: str, hypo: dict, settings: Settings | None = None) -> dict[str, Any]:
        dry = bool(hypo.get("_dry_run"))
        if not dry:
            try:
                s = settings or Settings()
                if s.gmgn_api_key and mint.endswith("pump"):
                    raise MissingFeatureError(f"HISTORICAL_UNAVAILABLE: bundler snapshot for {mint} at +30s not prospectively captured")
                raise MissingFeatureError(f"missing gmgn or not pump {mint}")
            except MissingFeatureError:
                raise
            except Exception as exc:
                raise MissingFeatureError(f"missing bundler feature for {mint}: {exc}") from exc
        import hashlib
        h = int(hashlib.sha256((mint + "bundler").encode()).hexdigest(), 16) % 10
        h2 = int(hashlib.sha256((mint + "bundler2").encode()).hexdigest(), 16) % 10
        bundler_wallets = h % 3
        copytrap_risk = "high" if h2 % 4 == 0 else "low"
        available_at = 30 + (h * 7) % 40
        observed_at = available_at + 5
        return {"bundler_wallets": bundler_wallets, "copytrap_risk": copytrap_risk, "mint": mint, "observed_at": observed_at, "available_at": available_at, "as_of_ts": available_at, "reconstructed_at": int(time.time()), "evidence_mode": "synthetic", "source": "bundler"}

    def should_enter(self, features: dict[str, Any], hypo: dict) -> bool:
        return features.get("bundler_wallets", 1) == 0 and features.get("copytrap_risk") != "high"

    def run(self, hypo: dict, settings: Settings | None = None, dry_run: bool = False) -> ExperimentResult:
        hypo = dict(hypo)
        hypo["_dry_run"] = dry_run
        return _run_walk_forward_with_filter(hypo, settings, experiment=self, dry_run=dry_run)


EXPERIMENTS: dict[str, BaseExperiment] = {
    "funder_repeat_hot_2_organic": FunderRepeatExperiment(),
    "early_holder_concentration_low": HolderConcentrationExperiment(),
    "funder_wallet_age_fresh": WalletAgeExperiment(),
    "bundler_avoid": BundlerAvoidExperiment(),
}

def get_experiment(name: str) -> BaseExperiment:
    return EXPERIMENTS.get(name) or FunderRepeatExperiment()
