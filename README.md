# smartalpha

Solana **pump.fun** 寄生聪明钱工具链：跨 mint 反推母钱包 → 实时 launch 监听 → paper 延迟税 → prove 裁决。

> 无 Web 前后端。接口 = CLI 子命令；核心 = `src/smartalpha/*`；报告 JSON 字段见下方契约。

## 安装

```bash
uv sync
cp .env.example .env   # 填 HELIUS_API_KEY
uv run smartalpha self-check
```

可选：`uv sync --extra dev` 后跑测试 / ruff。

## 主流程（闭环）

```
auto-discover  →  data/auto_discover.json  (candidates + recommended_funders + quality)
       ↓
watch-launches →  paper_signals 表 + 延迟快照
       ↓
paper-log health / export
       ↓
prove          →  PROVEN | PROMISING | INSUFFICIENT_DATA | FALSIFIED
```

```bash
uv run smartalpha auto-discover --min-gain 200 --limit 30
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

## 证明协议

`docs/PROOF_PROTOCOL.md`
