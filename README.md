# smartalpha

Solana **早期开盘第一性原理 Alpha 测量与狙击引擎**：实时 launch 监听 → 四大支柱微观结构过滤（流动性防线 + 买方订单流熵 + 换手加速度 + 摩擦力阻尼） → 真实延迟快照与滑点扣除 → prove 裁决。

> 无 Web 前后端。接口 = CLI 子命令；核心 = `src/smartalpha/*`；策略详细数学定义见 [`docs/STRATEGY_SPEC.md`](docs/STRATEGY_SPEC.md)。

## 策略四大核心支柱 (The 4 Pillars)

1. **流动性防线（Reserve $\ge \$3,000$）**：硬性剔除 81% 薄底池即时 Rug 盘，进出冲击控制在 $\le 1.6\%$。
2. **买方去重熵（Unique Buyers $\ge 8$）**：识破 Dev 单钱包对倒刷量，确保真实社区/Alpha 群扩散。
3. **换手加速度（$V_{180s} / \text{Reserve} \ge 0.5$）**：捕捉早期资金注入的“逃逸速度”。
4. **摩擦力与仓位阻尼（\$100~\$250 & 滑点 $\le 10\%$）**：动态扣除双向冲击、DEX 费率与 Gas/Jito 成本。

## 安装

```bash
uv sync
cp .env.example .env   # 填 HELIUS_API_KEY
uv run smartalpha self-check
```

## 主流程（闭环）

```
watch-launches (Helius WS / RPC 实时监听新 Mint)
       ↓
First-Principles Gate (底池 >= $3k & 买家 >= 8 & 换手 >= 0.5)
       ↓
paper_signals 表 (动态价格冲击 + 延迟快照 0/90/180/300/900s)
       ↓
prove (样本外 OOS 统计检验 + 实盘 Paper 门禁) → PROVEN | FALSIFIED
```

```bash
PYTHONUNBUFFERED=1 uv run smartalpha watch-launches
uv run smartalpha paper-log catch-up
uv run smartalpha paper-log health
uv run smartalpha prove data/auto_discover.json
```

## 命令

| 命令 | 作用 |
|------|------|
| `auto-discover` | 拉涨过的 mint → 追 funder → 写报告 |
| `watch-launches` | Helius WS 监听 Create → 分析 → paper |
| `paper-log` | `export` / `catch-up` / `list` / `health` |
| `prove` | walk-forward OOS + paper 门禁 |
| `scan-mint` / `trace-funders` | 单 mint 诊断 |
| `backtest-funders` / `walk-forward` | 历史回测 |
| `gmgn-cookie` | 导入/刷新 GMGN cookie |

## 报告字段契约（关键）

**auto_discover.json**
- `candidates[]`: `mint`, `source`, `gain_h24_pct`, `pair`, `url`
- `recommended_funders[]`: `address`, `label`, `weight`, `mints`, `quality.grade|win_rate|median_return_pct|rug_rate`

**paper_signals**（SQLite）
- `mint`, `signal_ts`, `recommendation`, `copytrap_risk`, `hot_organic_buyers`, `hot_funders_json`
- `strict_signal` (0/1), `price_usd` (t0), `snapshots_json` keys `"0"|"90"|"180"|"300"|"900"`

**prove_report.json**
- `verdict`, `phase1_historical.metrics.oos_signals|best_net_tpsl_sol`, `phase2_paper.metrics.paper_strict`

## 开发

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
uv run smartalpha self-check
```

## 服务器部署

见 `deploy/install.sh` 与 `deploy/*.service`。

```bash
# 服务器上
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Xeron2000/smartalpha.git ~/smartalpha
cd ~/smartalpha && bash deploy/install.sh
# 配置 .env 后：
mkdir -p ~/.config/systemd/user
cp deploy/smartalpha-watch.service deploy/smartalpha-paper-catchup.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now smartalpha-watch smartalpha-paper-catchup
```

**切勿**把 `.env` / `data/gmgn.cookie` 提交到 git。

## 文档与证明协议

- 策略与产品规范（第一性原理）：[`docs/STRATEGY_SPEC.md`](docs/STRATEGY_SPEC.md)
- 证明协议与验证门禁：[`docs/PROOF_PROTOCOL.md`](docs/PROOF_PROTOCOL.md)
- 研发设计：[`docs/research-v1-design.md`](docs/research-v1-design.md)

## License

[MIT](LICENSE)
