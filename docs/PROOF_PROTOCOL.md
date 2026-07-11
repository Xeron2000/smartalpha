# smartalpha 策略证明协议

目标：用可复核证据回答「寄生 funder 策略能不能赚钱」，而不是凭回测感觉。

## 裁决等级

| Verdict | 含义 | 能否上实盘资金 |
|---------|------|----------------|
| **PROVEN** | 历史 OOS + 实盘 paper 都过门 | 仅小仓 + 熔断 |
| **PROMISING** | 历史 OOS 过，paper 未完成 | **否** |
| **INSUFFICIENT_DATA** | 样本/质量 funder 不够，无法判定 | **否** |
| **FALSIFIED** | 有足够样本且 EV≤0 | **否，停策略** |

## Phase 1 — 历史 walk-forward（可立刻跑）

```bash
uv run smartalpha prove data/auto_discover_25.json
# 报告: data/prove_report.json
```

门禁：

1. train 窗发现的 funder，**grade ≥ medium** 至少 2 个  
2. test 窗 mint ≥ 3  
3. OOS strict 信号 ≥ 10  
4. 最佳出场模式 net SOL > 0（默认 15% 滑点）  
5. 胜率 ≥ 35%，或净利足够大（net > 1 SOL）可覆盖低胜率  

已知局限（报告里会写）：

- 价格用 DexScreener h1/h6/h24 **代理**，不是 90s 真实成交价  
- funder quality 的 h24 也是「此刻滚动 24h」，不是「开盘到出货」——会偏悲观  
- 从已涨 mint 反推 funder 有幸存者偏差  

→ Phase1 **最多**把裁决抬到 PROMISING，不能单独变成 PROVEN。

## Phase 2 — 实盘 paper（必须持续跑）

```bash
# 终端 A：持续监听（写入 paper_signals）
uv run smartalpha watch-launches

# cron 每 10 分钟
uv run smartalpha paper-log catch-up
uv run smartalpha paper-log export --out data/paper_signals.csv

# 有数据后重跑裁决
uv run smartalpha prove
```

门禁：

1. strict paper 信号 ≥ 30  
2. 用 **signal t0 价格 → 延迟价格** 算真实涨跌（不是 Dex 滚动 m5）  
3. 主观察窗口 300s（5m）净 EV > 0（含双边滑点假设）  
4. 延迟税可测（≥5 条完整快照）  

## 什么时候算「证明能赚」

仅当：

```text
uv run smartalpha prove  →  VERDICT: PROVEN
```

在此之前：

- 可以继续研究 / 扩样本 / 修 funder 定义  
- **不**应加大仓位或全自动执行  

## 扩样本（Phase1 不够时）

```bash
uv run smartalpha auto-discover --min-gain 200 --limit 40
uv run smartalpha prove data/auto_discover.json --min-oos-signals 10
```

## 质量 / 进场逻辑（2026-07-11 修订）

### Funder 质量
- **优先用 discovery gain**（auto-discover 选币时的涨幅），不再用 Dex「此刻滚动 h24」当 win/loss。  
- 老盘 live 路径改为 age-aware：年轻盘用短窗涨跌；>48h 用存活/流动性，避免 dump 后假死分。  
- 结果：同批 `auto_discover_25` 从 **0 个 grade≥medium** → **16/16 medium+**（discovery 重算）。

### 进场
- STRONG：≥N 个 hot organic + 非 high copytrap + pair + liq OK。  
- MEDIUM：≥1 hot organic（balanced 可入）。  
- **历史回测**：`BACKTEST_IGNORE_STALE_LIQ=1`（默认）——老盘当前低流动性 ≠ 入场时低流动性，不再一刀切滤掉。  
- 默认 `SIGNAL_MIN_LIQUIDITY_USD=5000`（原 10k 对早期池过严）。  
- 实盘仍要求真实 liq（不 ignore stale）。

### 注意
Discovery 质量在「全是涨过的 mint」上会偏乐观（幸存者）。**真正裁决仍看 OOS + paper**，质量闸门只负责别把 live-h24 误杀真母钱包。

## 2026-07-11 修订前基线（auto_discover_25，live-h24 质量）

| 项 | 结果 |
|----|------|
| Verdict | **INSUFFICIENT_DATA** |
| Train funders | 14 raw → **0** grade≥medium |
| OOS signals | **0** |
| paper_signals | **0** |

## 2026-07-11 扩样本 prove（auto_discover limit≈40）

数据：`data/auto_discover_40.json`，报告：`data/prove_report_40.json`

| 项 | n=25（修订后） | n=39（扩样本，canonical） |
|----|----------------|---------------------------|
| Candidates / traced | 25 | **39** |
| Recommended funders | 16 | **51**（27 strong / 24 medium） |
| Train funders grade≥medium | 11 | **34**（17s/17m） |
| Test OOS mints | 8 | **12** |
| Strict OOS signals | 4 | **3**（仍 << 10） |
| Best exit | dynamic **-0.62** wr=0 | **scale +1.46 SOL** wr=67%（n=3） |
| fixed/dynamic | 负 | 仍偏负（fixed -0.28） |
| Paper rows | 0 | 0 |
| Verdict | INSUFFICIENT_DATA | **INSUFFICIENT_DATA** |

报告：`data/prove_report_40.json`、`data/walk_forward_40.json`、`data/auto_discover_40.json`

解读：

1. 扩样本**未达 PROMISING**：信号 3 < 门槛 10，不能因 scale 单次正 EV 上仓。  
2. Funder 发现/质量侧已通（34 个 medium+）；瓶颈是 **OOS 可交易交集太稀**。  
3. scale 在 3 笔上 +1.46 有希望，但 fixed/dynamic 仍负，且价格是 Dex 代理 → **噪声极大**。  
4. 下一步：`watch-launches` paper ≥30；不要再盲目堆历史 mint 数量当证明。
