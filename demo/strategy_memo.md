# smartalpha 寄生策略备忘录

> 供外部评审（ChatGPT DevSpace / 人工）。不含密钥；地址为公开链上数据。
> 生成日期：2026-07-04

## 1. 项目定位

**smartalpha** = Solana pump.fun 上的 **寄生 smart money** 工具链。

目标不是 block-0 snipe，而是：

1. 从历史涨过的 mint 反推 **母钱包（cross-mint funder）**
2. 实时监听新 launch，等 early buyers 出现后 trace funded-by
3. 当 fresh wallet 被已知 hot funder 资助并买入 → 发出 **follow 信号**
4. 人工或小仓跟单；出场规则在回测中迭代

## 2. 核心假设（Alpha Thesis）

```
涨过的 mint → block 0~2 early buyers → 追 Funded-by
→ 跨 mint 重复出现的 funder = 母钱包
→ 新 launch：fresh wallet + 已知 funder + organic buy → 真信号
```

**与纯 sniper 的差异**：确认后再进（默认 settle 90s），牺牲速度换过滤 insider/bundler。

## 3. 数据管线

| 阶段 | 数据源 | 说明 |
|------|--------|------|
| 候选 mint | GMGN → DexScreener → Gecko | auto-discover |
| Early buyers | Helius RPC getTransaction | block 0~2 |
| Funded-by | Helius → Solscan Pro → RPC 回溯 | resolve_first_funder |
| 母钱包库 | cross-mint 聚合 | `funders.json`（当前 6 个） |
| 实时 launch | Helius logsSubscribe pump program | watch-launches |
| 回测价格 | DexScreener priceChange h1/h6/h24 | **代理，非 block 成交价** |

## 4. 进场规则（当前实现）

### strict（默认）

- `copytrap_risk != high`
- `recommendation == follow_cohort`
- `hot_funder_hits` 非空（命中 funders.json）
- `hot_organic_buyers >= 2`（hot funder 资助、非 bundler 的 buyer）
- Dex pair 存在
- `liquidity_usd >= 10000`（SIGNAL_MIN_LIQUIDITY_USD）

### balanced（可选）

- strict **或** `watch + >= 3 hot organic buyers`

### legacy（对照）

- 任意 hot funder + watch/follow_cohort

## 5. 出场规则（回测模式）

| 模式 | 逻辑 |
|------|------|
| **fixed** | TP100% / SL30% |
| **dynamic** | early h1 cut、stall h6、trail +50%/-30%、hard TP120/SL30 |
| **scale** | 翻倍卖 50%，余仓无止损到 h24 |
| **hybrid** | 未翻倍：early/stall/sl；到 2x 卖半；余仓 trail |
| **ladder** | 25%@2x、25%@3x、余仓 trail |
| **compare** | 同一批信号一次对比以上全部 |

环境：`BACKTEST_MAX_HOLD_MIN=30`（h1 代理时间止损），滑点 15%，仓位 0.5 SOL/笔。

## 6. 回测结果摘要

### 样本

- 来源：`auto-discover` 20 个 GMGN 涨过的 pump mint（2026-07-04）
- 母钱包：6 个 cross-mint funder（已剔除 CEX hot wallet）

### exit compare（strict + liq≥$10k，4 笔信号）

| 出场 | net PnL (SOL) | 胜/负 |
|------|---------------|-------|
| scale | **+2.96** | 2/2 |
| fixed | -0.56 | 1/3 |
| hybrid / ladder / dynamic | -0.86 | 0/4 |

### hybrid 全样本（5 笔信号，含低 liq）

- net 出场：**+1.57 SOL**（主要靠 7szut +1076%）
- rug 笔 hybrid early@h1：约 -0.15 SOL vs 满仓 h24 约 -0.55 SOL

## 7. 已知缺陷（评审重点）

1. **幸存者偏差**：从「已涨 mint」反推 funder，天然事后视角
2. **样本过小**：4~6 笔信号，无统计显著性
3. **价格代理粗糙**：DexScreener h1/h6/h24 ≠ 实际 entry；同一 mint 不同时间拉取结果会变
4. **90s 延迟未建模**：真 moon 常在前 30s，系统性晚入场
5. **母钱包可能是 farm**：cross-mint 重复 ≠ 聪明钱，可能是分发器
6. **出场未按置信分档**：scale 与 hybrid 用在同一信号上不合理
7. **walk-forward 已接入**：默认 chronological 70/30；见 `data/walk_forward.json`
8. **funder 质量评分**：train 窗 mint 的 win_rate / median_h24 / rug_rate → 调整 weight
9. **无实盘执行层**：缺 block 级成交价、Jito、同 block 竞争

## 8. 当前结论（作者自评）

| 维度 | 判断 |
|------|------|
| 工程闭环 | ✅ 可用（发现 → 监听 → 告警 → 回测） |
| Alpha 假设 | ⚠️  plausible，未充分验证 |
| 自动实盘 | ❌ 证据不足 |
| 下一步 | paper 30 天 protocol + 按置信分档出场 |

## 9. 文件索引（demo/ 目录）

| 文件 | 内容 |
|------|------|
| `strategy_memo.md` | 本文 |
| `exit_compare.json` | 五种出场对比 |
| `backtest_summary.json` | hybrid 回测摘要 |
| `trade_cases.json` | 6 个典型案例（带评审问题） |
| `funders_summary.json` | 母钱包库摘要（含 quality 评分） |
| `walk_forward.json` | OOS walk-forward 报告 |
| `CHATGPT_TASK.md` | 可直接粘贴的 Task Packet |
