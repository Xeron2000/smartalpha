# smartalpha 策略证明协议

目标：用可复核证据回答「四柱微观结构入口是否有净正期望」，而不是凭回测感觉。funder/聪明钱只保留为历史研究标签。

## 裁决等级

| Verdict | 含义 | 能否上实盘资金 |
|---------|------|----------------|
| **PROVEN** | 历史 OOS + 实盘 paper 都过门 | 仅小仓 + 熔断 |
| **PROMISING** | 历史 OOS 过，paper 未完成 | **否** |
| **INSUFFICIENT_DATA** | 可交易样本或数据质量不足，无法判定 | **否** |
| **FALSIFIED** | 有足够样本且 EV≤0 | **否，停策略** |

## Phase 1 — 历史 walk-forward（可立刻跑）

```bash
uv run smartalpha prove data/oos_candidates.json
# 数据集必须显式标记 metadata.split=oos，并包含信号时点特征
```

门禁：

1. 数据集显式声明 OOS，且特征时间不晚于信号时间
2. 信号时点 liquidity、unique buyers、买卖笔数、成交量、买家集中度齐全
3. OOS strict 可闭合结果 ≥ 10
4. 配置的出场模式扣除滑点后净 EV > 0
5. 胜率 ≥ 35%

已知局限（报告里会写）：

- 历史 h1/h6/h24 若来自 DexScreener 仍是**代理**，不能替代信号时点成交与价格路径
- funder quality 的 h24 也是「此刻滚动 24h」，不是「开盘到出货」——会偏悲观  
- 从已涨 mint 反推 funder 有幸存者偏差  

→ Phase1 **最多**把裁决抬到 PROMISING，不能单独变成 PROVEN；当前 `prove.py` 会实际调用 strict 入口和配置的 exit 模拟。

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
2. 用 **signal t0 → 延迟价格** 算涨跌；DexScreener 仅作代理，执行级证明必须用 signer/on-chain quote
3. 主观察窗口 300s（5m）净 EV > 0（含双边滑点假设）  
4. 延迟税可测（≥5 条完整快照）  

## 什么时候算「证明能赚」

仅当：

```text
uv run smartalpha prove  →  VERDICT: PROVEN
```

在此之前：

- 可以继续研究 / 扩样本 / 修 funder 定义  
- **不**应加大仓位或开启 Canary 自动执行

## 扩样本（Phase1 不够时）

生成一个符合 canonical 字段和 `metadata.split=oos` 的 OOS 数据集后直接验证：

```bash
uv run smartalpha prove data/oos_candidates.json --min-oos-signals 10
```

## 当前入口契约

- STRONG：pair、明确 launch timestamp、liquidity、Unique Buyers、买卖笔数、买家集中度、V5m/Reserve 全部通过。
- MEDIUM/WATCH：仅观察，不允许自动下单。
- funder/聪明钱字段只作 legacy 研究标签，不能绕过微观结构门禁。
- Paper 证明使用 signal t0 → 300s；只有 `onchain_quote` 或 `signer_quote` 才能作为执行级证据。

### 注意
从已涨 mint 反推 funder 仍有幸存者偏差。历史代理价格只能支持研究，不能支持 Canary 自动执行。

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
