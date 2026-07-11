# smartalpha — ChatGPT DevSpace 评审包

本目录仅含**可公开分享**的策略摘要与回测数据，无 API 密钥。

## 文件

| 文件 | 用途 |
|------|------|
| `strategy_memo.md` | 策略说明（先读） |
| `CHATGPT_TASK.md` | 复制到 ChatGPT 的 Task Packet |
| `exit_compare.json` | 出场模式对比 |
| `backtest_summary.json` | hybrid 回测摘要 |
| `trade_cases.json` | 典型案例 + 评审问题 |
| `funders_summary.json` | 母钱包库摘要（含 quality 评分） |
| `walk_forward.json` | chronological OOS 报告 |
| `events.json` | 离线 cluster demo 数据（与 funder 策略无关） |

## 启动 DevSpace

```bash
cd /home/xeron/Coding/crypto
scripts/start-chatgpt-devspace.sh
```

将输出的 MCP URL 配到 ChatGPT App，Workspace 指向本目录。

## 更新评审包

本地 agent 在跑完新回测后，可同步：

```bash
cp data/exit_compare.json demo/exit_compare.json
# 并更新 backtest_summary.json / trade_cases.json 中的数字
```

## 回传

把 ChatGPT 输出粘给 Cursor agent；仅采纳有证据、可验证的建议。
