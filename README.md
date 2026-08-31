# smartalpha

Solana 早期开盘第一性原理 Alpha 测量与执行门。基于微观结构（流动性防线 + 买方订单流熵 + 成交速度 + 摩擦力阻尼）实时识别正期望开盘；真实交易由独立 signer 承担，默认只做 Paper。

策略详细数学定义：[`docs/STRATEGY_SPEC.md`](docs/STRATEGY_SPEC.md) | 证明协议：[`docs/PROOF_PROTOCOL.md`](docs/PROOF_PROTOCOL.md) | signer 契约：[`docs/EXECUTION_CONTRACT.md`](docs/EXECUTION_CONTRACT.md)

---

## 四大核心支柱 (The 4 Pillars)

1. **流动性防线（代码默认 Reserve $\ge \$3,000$）**：硬性剔除 81% 薄底池即时 Rug 盘，进出冲击控制在 $\le 1.6\%$。
2. **买方去重熵（Unique Buyers $\ge 8$）**：识破 Dev 单钱包对倒刷量，确保真实社区/Alpha 群扩散。
3. **成交速度（$V_{5m} / \text{Reserve} \ge 0.5$）**：使用信号时可观测的 DexScreener 5 分钟成交量，捕捉早期资金注入速度。
4. **摩擦力阻尼（固定 0.05 SOL & 最大滑点 $\le 5\%$）**：动态扣除双向冲击、DEX 费率与 Gas/Jito 成本。

---

## 快速开始

```bash
uv sync
cp .env.example .env   # 填入 HELIUS_API_KEY
uv run smartalpha self-check
```

---

## 核心工作流

```text
Helius WS (watch-launches)
       ↓
四大支柱微观结构门禁 (Reserve >= $3k, Buyers >= 8, V5m/Res >= 0.5)
       ↓
Paper / Shadow / Canary 执行门 (独立 signer，默认不广播)
       ↓
统计证明与风控裁决 (prove → PROVEN | FALSIFIED)
```

```bash
# 1. 启动实时监听（默认 EXECUTION_MODE=paper，不会下单）
PYTHONUNBUFFERED=1 uv run smartalpha watch-launches

# 2. 补齐快照与健康检查
uv run smartalpha paper-log catch-up
uv run smartalpha paper-log health

# 3. 导出考虑摩擦力后的 Paper 净收益估算 CSV
uv run smartalpha paper-log export --out data/paper_signals.csv

# 4. 执行统计证明裁决
uv run smartalpha prove
```

---

## CLI 命令

| 命令 | 说明 |
|------|------|
| `watch-launches` | 实时监听 pump.fun 开盘并执行四大支柱微观结构过滤 |
| `paper-log` | `export` / `catch-up` / `list` / `health`（延迟快照与净收益追踪） |
| `prove` | 样本外 OOS 统计检验 + 实盘 Paper 门禁裁决 |
| `execution` | 查看执行模式、订单和持仓；Canary 需独立 signer |
| `scan-mint` | 单 Token 早期流动性、买家去重与微观结构诊断 |
| `self-check` | 本地全流程离线自检 |

---

## 开发与验证

```bash
uv run pytest -q
uv run ruff check src tests
uv run smartalpha self-check
```

## License

[MIT](LICENSE)
