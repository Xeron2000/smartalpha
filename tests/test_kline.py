from smartalpha.exit_rules import sim_fixed_tp_sl  # noqa: I001
from smartalpha.providers.gmgn import kline_to_gains

def _kline_30s(entry=1.0, closes=None):
    # build 30s candles: each candle dict with c/close
    closes = closes or []
    kline = [{"c": entry, "h": entry, "l": entry}]
    for c in closes:
        kline.append({"c": c, "h": max(entry, c), "l": min(entry, c)})
    # pad to at least 35 for 900s
    while len(kline) < 35:
        kline.append({"c": kline[-1]["c"], "h": kline[-1]["h"], "l": kline[-1]["l"]})
    return kline

def test_kline_gains_30s_basic():
    k = _kline_30s(entry=1.0, closes=[1.0, 1.0, 1.5, 1.2, 1.1, 1.0, 1.0, 1.0, 1.0, 2.0])
    # k[0]=entry 1.0, k[3]=1.5 -> 90s (3*30) should be +50%
    gains = kline_to_gains(k, "30s")
    assert gains is not None
    assert abs(gains["gain_90_pct"] - 50.0) < 1e-6
    assert abs(gains["gain_300_pct"] - 100.0) < 1e-6  # k[10]=2.0
    assert gains["kline_interval"] == "30s"
    assert "mfe_pct" in gains and "mae_pct" in gains

def test_kline_gains_1m_mapping():
    # 1m interval: 90s ~ idx1, 180s=3, 300s=5, 900s=15
    k = [{"c": 1.0, "h": 1.0, "l": 1.0}]
    for i in range(1, 20):
        v = 1.0 + i * 0.1
        k.append({"c": v, "h": v, "l": 1.0})
    gains = kline_to_gains(k, "1m")
    assert gains is not None
    # 90s idx 2 (round(90/60)=2) -> c=1.2 (+20%)
    assert abs(gains["gain_90_pct"] - 20.0) < 1e-6
    assert abs(gains["gain_180_pct"] - 30.0) < 1e-6  # idx3=1.3
    assert gains["kline_interval"] == "1m"

def test_kline_mfe_mae():
    k = [{"c": 1.0, "h": 1.5, "l": 0.8}, {"c": 1.2, "h": 2.0, "l": 0.9}, {"c": 1.1, "h": 1.3, "l": 0.5}]
    while len(k) < 35:
        k.append({"c": 1.0, "h": 1.0, "l": 1.0})
    gains = kline_to_gains(k, "30s")
    assert gains["mfe_pct"] == 100.0  # (2.0-1.0)/1.0
    assert gains["mae_pct"] == -50.0  # (0.5-1.0)/1.0

def test_kline_tp_sl_ordering_via_sim():
    gains = {"h1": 50, "h6": 120, "h24": 80}
    pnl, reason = sim_fixed_tp_sl(gains, 100, 30, 0.5, 0.15)
    assert reason == "tp@h6"  # 120 hits tp first
    gains2 = {"h1": -40, "h6": 50, "h24": -90}
    pnl2, reason2 = sim_fixed_tp_sl(gains2, 100, 30, 0.5, 0.15)
    assert reason2 == "sl@h1"  # -40 triggers sl before tp
