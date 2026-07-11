#!/usr/bin/env python3
"""smartalpha strategy statistical analysis - fully reproducible."""
import json, math, random
from pathlib import Path

random.seed(42)

backtest = json.loads(Path("data/funder_backtest.json").read_text())
btrades = backtest["trades"]
signals = [t for t in btrades if t["signaled"]]
watches = [t for t in btrades if t["recommendation"] == "watch"]
position = 0.5

print("=" * 72)
print("1. 统计显著性分析")
print("=" * 72)

tpsl_pnls = [(t["mint"][:8], t["pnl_tpsl_sol"]) for t in signals]
print("")
print("全部 %d 笔信号 TPSL PnL:" % len(signals))
for m, p in tpsl_pnls:
    print("   %s  %+.4f SOL" % (m, p))

net_all = sum(p for _, p in tpsl_pnls)
tpsl_pnls_sorted = sorted(tpsl_pnls, key=lambda x: x[1], reverse=True)
moon_mint, moon_pnl = tpsl_pnls_sorted[0]
net_excl_moon = net_all - moon_pnl
print("")
print("净 TPSL: %+.4f SOL" % net_all)
print("去掉最大赢家 %s (%+.4f SOL): %+.4f SOL" % (moon_mint, moon_pnl, net_excl_moon))

h24_pnls = [(t["mint"][:8], t["pnl_h24_sol"]) for t in signals]
print("")
print("H24 PnL:")
for m, p in h24_pnls:
    print("   %s  %+.4f SOL" % (m, p))
net_h24 = sum(p for _, p in h24_pnls)
print("净 H24: %+.4f SOL" % net_h24)
net_h24_excl = net_h24 - moon_pnl
print("去掉最大赢家 H24: %+.4f SOL" % net_h24_excl)

# Bootstrap
returns = [p["pnl_tpsl_sol"] / position for p in signals]
n_sig = len(signals)

print("")
print("每笔 ROI (pnl/position):")
for t, r in zip(signals, returns):
    print("   %s  ROI=%+.2f%%" % (t["mint"][:8], r * 100))
print("   mean ROI = %+.2f%%" % (sum(returns) / len(returns) * 100))


def bootstrap_ci(data, n=100000, ci=0.95):
    means = []
    medians = []
    for _ in range(n):
        s = random.choices(data, k=len(data))
        means.append(sum(s) / len(s))
        medians.append(sorted(s)[len(s) // 2])
    means.sort()
    medians.sort()
    alpha = (1 - ci) / 2
    lo = int(alpha * n)
    hi = int((1 - alpha) * n)
    return {"mean_ci": (means[lo], means[hi]), "median_ci": (medians[lo], medians[hi])}


boot = bootstrap_ci(returns)
print("")
print("Bootstrap 均值 95%% CI: [%+.2f%%, %+.2f%%]" % (boot["mean_ci"][0] * 100, boot["mean_ci"][1] * 100))

# 模拟 10000 个周期
period_outcomes = []
pos_count = 0
for _ in range(10000):
    period_pnl = sum(random.choices(returns, k=n_sig)) * position
    period_outcomes.append(period_pnl)
    if period_pnl > 0:
        pos_count += 1
period_outcomes.sort()
plo = period_outcomes[250]
phi = period_outcomes[9749]
pmean = sum(period_outcomes) / 10000
print("")
print("模拟 10000 个 %d-mint 周期:" % n_sig)
print("   期望收益均值: %+.4f SOL" % pmean)
print("   95%% 区间: [%+.4f, %+.4f] SOL" % (plo, phi))
print("   盈利概率: %.1f%%" % (pos_count / 10000 * 100))

# 贝叶斯
a_prior, b_prior = 1, 1
a_post = a_prior + backtest["wins_tpsl"]
b_post = b_prior + backtest["losses_tpsl"]
print("")
print("贝叶斯后验胜率 Beta(%d,%d):" % (a_post, b_post))
print("   后验均值: %.2f%%" % (a_post / (a_post + b_post) * 100))

import subprocess
r = subprocess.run(["python3", "-c", "from scipy.stats import beta\na, b = " + str(a_post) + ", " + str(b_post) + "\nprint(beta.ppf(0.025, a, b), beta.ppf(0.975, a, b))"], capture_output=True, text=True)

if r.returncode == 0:
    try:
        parts = r.stdout.strip().split()
        lo_b, hi_b = float(parts[0]), float(parts[1])
        print("   95%% 可信区间: [%.2f%%, %.2f%%]" % (lo_b * 100, hi_b * 100))
    except:
        approx_se = math.sqrt(a_post * b_post / ((a_post + b_post) ** 2 * (a_post + b_post + 1)))
        approx_mean = a_post / (a_post + b_post)
        print("   95%% 近似区间: [%.2f%%, %.2f%%]" % ((approx_mean - 1.96 * approx_se) * 100, (approx_mean + 1.96 * approx_se) * 100))
else:
    approx_se = math.sqrt(a_post * b_post / ((a_post + b_post) ** 2 * (a_post + b_post + 1)))
    approx_mean = a_post / (a_post + b_post)
    print("   95%% 近似区间: [%.2f%%, %.2f%%]" % ((approx_mean - 1.96 * approx_se) * 100, (approx_mean + 1.96 * approx_se) * 100))

print("")
print("pump.fun 生态背景:")
print("   日均 ~5000+ launch，20 mint 样本覆盖率 = %.4f%%" % (20 / 5000 * 100))
print("   信号率: %.0f%% -> 若应用于全量，日均 ~%d 信号" % (5 / 20 * 100, int(5000 * 5 / 20)))
print("   但策略仅扫描 top gainer (DexScreener/GMGN rank)，进入筛选管道的")
print("   mint 已经是异常值。这是 survivorship/selection bias 的关键来源。")

# ========== Section 2 ==========
print("")
print("=" * 72)
print("2. 出场模式比较")
print("=" * 72)

exit_compare = json.loads(Path("data/exit_compare.json").read_text())
print("")
print("Exit compare (20 mint, %d 信号, %d liq 过滤):" % (exit_compare["signals"], exit_compare["liquidity_filtered"]))
for r in exit_compare["ranked"]:
    print("   %8s: %+.4f SOL  (%dW/%dL)" % (r["mode"], r["net_tpsl_sol"], r["wins"], r["losses"]))

demo_exit = json.loads(Path("demo/exit_compare.json").read_text())
print("")
print("Exit compare (demo, 20 mint, %d 信号, %d liq 过滤):" % (demo_exit["signals"], demo_exit["liquidity_filtered"]))
for r in demo_exit["ranked"]:
    print("   %8s: %+.4f SOL  (%dW/%dL)" % (r["mode"], r["net_tpsl_sol"], r["wins"], r["losses"]))

print("")
print("出场模式公平性分析:")
print("   scale 在 4 笔信号中表现最好 (+2.96 SOL, 2W/2L)。")
print("   但主回测 hybrid 对 20/4 笔 real 回撤事件做了 early cut")
print("   (BK6nc early@h1 -0.1554 vs h24 -0.5559; Fnvuz -0.1557 vs -0.5503)")
print("   在 demo exit_compare 中, scale 对同 1 笔损失最大化 (-0.4735 SOL)")
print("   结论: scale 在低样本下极易被 singular 赢家支配，不稳健。")
print("   核心分歧: early cut 是保护(止损 rug 从 -0.55 缩到 -0.15)")
print("   vs 可能提前砍掉反转币。但本数据中没有反转案例。")

watches_only = [t for t in btrades if t["recommendation"] == "watch" and not t["signaled"]]
print("")
print("不同 exit 与信号置信度:")
print("   当前约束: 5/5 信号严格 same entry rule (min_hot_buyers=2, follow_cohort)")
print("   额外 watch 信号 (n=%d):" % len(watches_only))
for t in watches_only:
    g = t["gain_h24_pct"]
    print("      %s  h24=%+.2f%%  buyers=%d  funders=%s" % (t["mint"][:8], g or 0, t["hot_organic_buyers"], t["hot_funders"]))

# ========== Section 3 ==========
print("")
print("=" * 72)
print("3. Walk-Forward 解读")
print("=" * 72)

wf = json.loads(Path("data/walk_forward.json").read_text())
print("")
print("Walk-forward 配置:")
print("   训练集: %d mint" % len(wf["train_mints"]))
print("   测试集: %d mint" % len(wf["test_mints"]))
print("   训练发现: %d funder" % len(wf["train_funders"]))
print("   测试信号: %d" % wf["test_compare"]["signals"])
print("   测试 liq 过滤: %d" % wf["test_compare"]["liquidity_filtered"])

print("")
print("训练集 funder 质量:")
for f in wf["train_funders"]:
    q = f["quality"]
    print("   %s (%s): win_rate=%.0f%% median_h24=%+.1f%% rug_rate=%.0f%% count=%d" %
          (f["address"][:8], f["label"], q["win_rate"] * 100, q["median_h24_pct"], q["rug_rate"] * 100, q["mint_outcomes"]))

print("")
print("过拟合分析:")
print("   14 mint 发现 12 funder (密集度 %.1f funder/mint)" % (12 / 14))
print("   意味着大部分 mint 的 buyer 集高度重叠，发现的模式可能是")
print("   同一小撮人在所有 mint 里冒泡，而非有预测力的信号")
print("   训练集 12 funder 中 8/12 win_rate=0；仅 2 个 median_h24>0")
print("   尽管信号质量差，训练集仍有 5 个信号(来自这些 funder 的 overlap)")
print("   测试集 0 信号：要么 funder 停止了活动，要么仅在一小段时间活跃")
print("   -> 明确的过拟合证据：funder 模式不泛化到 out-of-sample")

# ========== Section 4 ==========
print("")
print("=" * 72)
print("4. Liquidity Filter 影响")
print("=" * 72)

bk6 = next(t for t in btrades if t["mint"].startswith("BK6"))
fnv = next(t for t in btrades if t["mint"].startswith("Fnv"))
print("")
print("Compare run 中 6/20 被 liq<$10k 过滤")
print("   BK6nc: liquidity ~ $3014, rug -95.5%%, early cut -0.155 SOL")
print("   Fnvuz: liquidity ~ $3568, rug -94.18%%, early cut -0.1557 SOL")
print("   过滤器保护了: 这两笔若 full h24 hold 会亏 %+.4f SOL" % (bk6["pnl_h24_sol"] + fnv["pnl_h24_sol"]))
print("   实际 post-filter 没进，避免了 %+.4f SOL" % (bk6["pnl_tpsl_sol"] + fnv["pnl_tpsl_sol"]))
print("   过滤器错过了什么? 没有低 liq 但涨的案例")
print("   净保护: %.4f SOL (这两笔)" % abs(sum([bk6["pnl_tpsl_sol"], fnv["pnl_tpsl_sol"]])))
print("   但注意: 过滤器也让全量扫描从 20->14 mint，减少信号机会")

# ========== Section 5 ==========
print("")
print("=" * 72)
print("5. 实盘信号 / 概率优势")
print("=" * 72)

pos_count_scan = sum(1 for t in btrades if (t["gain_h24_pct"] or 0) > 0)
neg_count = sum(1 for t in btrades if (t["gain_h24_pct"] or 0) is not None and t["gain_h24_pct"] < 0)
print("")
print("关键: pump.fun 新币 90s 后 80%% 归零，但本数据集的 mint 都来自")
print("GMGN/dexscreener top gainer rank -- 它们已经是幸存者偏差:")
print("   20 mint 中 24h 后正收益: %d/%d" % (pos_count_scan, 20))
print("   正收益: %d, 负收益: %d" % (pos_count_scan, neg_count))
print("   整体胜率(scan level): %.0f%%" % (pos_count_scan / 20 * 100))
print("   信号组胜率: %d/%d = %.0f%%" % (backtest["wins_tpsl"], backtest["wins_tpsl"] + backtest["losses_tpsl"], backtest["win_rate_tpsl"] * 100))

print("")
print("贝叶斯胜率对比:")
for baseline in [0.20, 0.30, 0.40, 0.50]:
    a_prior_m = max(1, baseline * 20)
    b_prior_m = max(1, (1 - baseline) * 20)
    a_post_m = a_prior_m + backtest["wins_tpsl"]
    b_post_m = b_prior_m + backtest["losses_tpsl"]
    pmean = a_post_m / (a_post_m + b_post_m)
    print("   基线 %.0f%%: posterior mean = %.2f%% (提升 %+.1f%%)" % (baseline * 100, pmean * 100, (pmean - baseline) * 100))

# ========== Section 6 ==========
print("")
print("=" * 72)
print("6. 优化建议（量化）")
print("=" * 72)

print("")
print("6a. 所需样本量 (二项检验):")
print("   观察到 1/5 win, win_rate=20%%")
print("   若真实 win_rate=X (vs H0: 50%%):")
for wr_hyp in [0.25, 0.30, 0.35, 0.40]:
    for power in [0.80, 0.90]:
        z_alpha = 1.96
        z_beta = 1.28 if power == 0.80 else 0.84
        p0 = 0.5
        p1 = wr_hyp
        num = (z_alpha * math.sqrt(p0 * (1 - p0)) + z_beta * math.sqrt(p1 * (1 - p1))) ** 2
        denom = (p1 - p0) ** 2
        n = int(num / denom) + 1
        print("   win_rate=%.0f%%, power=%.0f%%: n >= %d" % (wr_hyp * 100, power * 100, n))

print("")
print("6b. 盈亏平衡胜率:")
print("   单笔期望收益 = P(win)*avg_win + (1-P(win))*avg_loss - fee")
wins = [t["pnl_tpsl_sol"] for t in signals if (t["pnl_tpsl_sol"] or 0) > 0]
losses = [t["pnl_tpsl_sol"] for t in signals if (t["pnl_tpsl_sol"] or 0) < 0]
avg_win = sum(wins) / len(wins) if wins else 0
avg_loss = sum(losses) / len(losses) if losses else 0
fee_per = position * 0.15
print("   从数据: avg_win=%+.4f, avg_loss=%+.4f (hybrid exit)" % (avg_win, avg_loss))
print("   slippage cost/笔 = %.4f SOL" % fee_per)
if avg_win - avg_loss != 0:
    be_p = (fee_per - avg_loss) / (avg_win - avg_loss)
    print("   盈亏平衡 win_rate = %.1f%%" % (be_p * 100))
    print("   目前 win_rate=%.0f%%, %s盈亏平衡" %
          (backtest["win_rate_tpsl"] * 100,
           "已达" if backtest["win_rate_tpsl"] >= be_p else "未达"))

print("")
print("6c. 建议参数敏感性分析:")
print("   min_hot_buyers: [1, 2, 3, 4]")
print("   min_liquidity_usd: [0, 5000, 10000, 20000]")
print("   settle_sec: [30, 60, 90, 120]")
print("   exit_mode: 已做 5-way 对比")
print("   建议网格搜索:")
for mhb in [1, 2, 3]:
    for mliq in [0, 5000, 10000]:
        print("      (min_hot_buyers=%d, min_liquidity=$%d)" % (mhb, mliq))

print("")
print("6d. 从损失厌恶角度:")
total_loss = abs(sum(losses))
total_win = sum(wins)
print("   4 笔亏损总额: %.4f SOL" % total_loss)
print("   1 笔盈利总额: %.4f SOL" % total_win)
print("   盈亏比 (avg_win/|avg_loss|): %.2fx" % (avg_win / abs(avg_loss)))
print("   这是典型的高偏度策略: 多数小亏 + 偶尔大赢")
print("   关键风险: 若最大赢家不出现在样本外")
print("     Bootstrap 盈利概率 %.0f%%" % (pos_count / 10000 * 100))

print("")
print("=" * 72)
print("综合结论")
print("=" * 72)

print("""
1. 统计显著性: 不显著。n=5, 1W/4L, 去掉最大赢家净 %.4f SOL。
   bootstrap 区间跨越 0。贝叶斯胜率后验均值仅 ~%.0f%%。
   在 pump.fun 5000+/d 的生态里，20 mint 样本说明不了任何问题。

2. 出场模式: 4 笔信号的对比不可靠。scale 表现好纯属运气--同笔信号
   在 demo run 里 scale 是亏损最大的。hybrid 的 early cut 确实减少了 rug 损失。
   建议: scale 需要更多样本才能评估；hybrid 有更成熟的保护逻辑。

3. Walk-forward: 明确的过拟合。14 mint 挖出 12 funder，训练集信号多来自
   同一小撮人(交叉重叠)，测试集 6 mint 零信号。
   但要注意: 测试集零信号本身不否定策略(funders 可能恰好休假)，
   win_rate 接近 0 才是更强否定。

4. Liquidity 过滤: 保护了约 %.4f SOL 损失(两笔 rug)，没有错过 gainer。
   $10k 阈值似乎合理，但样本太小，不能确定是最优边界。

5. 概率优势: 策略信号胜率 (%.0f%%) 低于整体 top-gainer 扫描胜率 (%.0f%%)。
   说明当前 funder 规则实际上降低了胜率(但提高了盈亏比)。
   盈亏比 %.2fx 对高偏度策略来说尚可，但需要更高胜率配合。

6. 优化方向:
   - 至少需要 30-50 笔 paper trade 达到统计显著性
   - 盈亏平衡需要 ~%.0f%% 胜率(当前 %.0f%%)
   - 加入动态 exit: 高置信信号用 scale/ladder，低置信用 hybrid
   - funder 质量加权: 当前只 count cross-mint，没有计入历史 PnL
   - 建议增加时间过滤: funder 必须最近 24h 活跃才有效
""" % (net_excl_moon,
       a_post / (a_post + b_post) * 100,
       abs(bk6["pnl_tpsl_sol"] + fnv["pnl_tpsl_sol"]),
       backtest["win_rate_tpsl"] * 100,
       pos_count_scan / 20 * 100,
       avg_win / abs(avg_loss) if avg_loss != 0 else float('inf'),
       be_p * 100 if avg_win - avg_loss != 0 else 0,
       backtest["win_rate_tpsl"] * 100))
