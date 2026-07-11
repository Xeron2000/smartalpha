# Task Packet — 粘贴到 ChatGPT DevSpace

## Task

评审 **smartalpha** 寄生策略是否具备实盘 edge。重点：

1. 幸存者偏差（从已涨 mint 反推 funder）
2. 90s settle 延迟 vs confirm-then-enter 定位
3. 出场是否应按信号置信分档（scale vs hybrid）
4. 最小样本量 & paper trading 方案

## Workspace

`/home/xeron/Coding/crypto/demo`

## Permission

Read/list/search only.
Do not use Bash unless explicitly approved.
Do not edit files.
Do not delete files.
Do not install dependencies.
Do not run git commands.
Do not inspect secrets or files outside the workspace.

## What to inspect

1. `strategy_memo.md` — 策略全貌
2. `exit_compare.json` — 五种出场对比（4 信号，liq≥$10k）
3. `backtest_summary.json` — hybrid 回测 + 漏单
4. `trade_cases.json` — 6 个案例 + review_questions
5. `funders_summary.json` — 母钱包库

## What to answer

1. **Final verdict**：Go / Paper-only / No-go for live trading（一句话 + 置信度）
2. **Evidence inspected**：引用了哪些文件/数字
3. **Must-fix issues**（阻塞实盘，≤5 条）
4. **Should-fix issues**（提升 EV，≤5 条）
5. **Recommended strategy config**：进场 + 出场 + 仓位 + 风控（表格）
6. **Paper trading protocol**：30 天验证步骤、通过标准
7. **Answer each `review_questions`** in `trade_cases.json` briefly
8. **Suggested verification commands** for local agent（只读/回测，不含密钥）

## Context the reviewer should know

- Chain: Solana pump.fun memecoins
- Backtest uses DexScreener h1/h6/h24 as price proxy, 15% slippage, 0.5 SOL/trade
- Current hot funder count: 6
- Strict signals in best compare run: 4 trades; scale net +2.96 SOL, hybrid net -0.86 SOL
