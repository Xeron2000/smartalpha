# SmartAlpha V1 研究机器 — 架构设计 (2026-08-23)

## 1. 现状与痛点
- `gmgn_cookie.py` 抓 `gmgn.ai/defi/...` + Cloudflare，需手动贴 cookie，httpx 403，只能 curl。
- 历史价格用 `DexScreener h1/h6/h24` 滚动窗口代理，非 t0→90/180/300 真实价，TP/SL 排序/MFE/MAE 失真，prover 报告已明确 caveat。
- 无统一 provider 层：mint_sources/funder_score/funder/launch_intel 各自直调 DexScreener/GMGN/RPC，无 `source/observed_at`。
- 无 Launch Feature Snapshot：t0 发现后的 +10s/+30s/+90s/+300s 特征未冻结，存在 look-ahead 风险。
- 无 Research 闭环：hypothesis 无 `falsification_condition`、无 Reviewer/Red Team、无 Memory/Leaderboard、`prove` 仅跑 walk-forward+paper 计数。

## 2. 目标 (`smartalpha research cycle --dry-run` 可跑通)
```
Research Memory (data/research/memory.json)
 → Generate Hypotheses (带 falsification_condition, 去重已证伪)
 → Experiments → Historical / Walk-forward OOS / Latency+Slippage / Robustness
 → Reviewer (Feature Timestamp / Leakage / Metric)
 → Red Team (survivorship/selection/tiny-N/stale liq/label drift/overfit)
 → Paper 门禁 (≥30 strict，真实延迟价)
 → PROVEN / FALSIFIED / PROMISING / INSUFFICIENT_DATA
 → Leaderboard (data/research/leaderboard.json) + Persist Memory
```

## 3. Providers 抽象
```
src/smartalpha/providers/
  gmgn.py        # 官方 OpenAPI，Header: Authorization / x-api-key(以官方文档为准)，GMGN_API_KEY 来自 env
  solana.py      # RPC 封装：transfer provenance / first funder / signatures / transaction
  dexscreener.py # 仅 fallback + cross-validation，不再作 OOS 主价格源
```
- 统一返回信封：`{ data, source: "gmgn"|"solana"|"dexscreener", observed_at: int(ts), raw? }`
- 所有研究落盘 JSON/DB `snapshots_json` 强制含 `source/observed_at`，缺失则 Reviewer 拦。
- 错误归一：限流/超时/403 统一重试+指数退避，无 Key 时抛清晰 `MissingGMGNKeyError`。

### GMGN OpenAPI 映射 (以官方文档为准，需真 Key 校验)
- Trenches/Trending/NewPairs → `find_candidate_mints`
- Token/Pool/Security → Snapshot +10s
- Holders/Traders/Smart Money/Sniper/Bundler → Snapshot +30s
- 30s/1m Kline → 历史价格与 Paper 延迟税主源 (`GET /defi/quotation/v1/tokens/kline?interval=30s|1m`)
- Wallet Stats/Activity → Hypothesis 特征富化

## 4. P0 价格重建
- 新增 `providers/gmgn.py:get_kline(mint, interval, limit, observed_at)`。
- 重构 `backtest.py / backtest_funders.py / prove.py / walk_forward.py` 价格路径：
  `dex_pair_meta.h1/h6/h24` → `kline[t0 + 90/180/300/900]` 真实价，TP/SL 按 Kline 高低点排序判定，计算 MFE/MAE。
- 保留 DexScreener 仅作 fallback 交叉验证，`prove` 报告移除 proxy caveat，改为 kline 来源标注。
- 单测用固定 fixture 校验 TP/SL 触发顺序与 MAE/MFE。

## 5. P1 Snapshot 流水线
- `launch_watch.process_new_mint` 在 t0 后调度：
  +10s: `Token/Pool/Security` → +30s: `Holders/Traders/Smart Money` → +90s: 冻结 Entry Features (copytrap/hot_organic/bundler) → +300s: outcome (Kline 价)
- 快照写入 `paper_signals.snapshots_json` 扩展键或 `data/research/snapshots/{mint}.json`，每段带 `observed_at`。
- 单测校验时间戳严格递增且 90s 后 Entry 不可变 (look-ahead 防护)。

## 6. P2 Benchmark
- 新模块 `research/benchmark.py` 并行记录 `Helius logsSubscribe` 与 `GMGN trenches poll` 捕获集合。
- 产出 `data/research/benchmark_gmgn_vs_helius.json`：`recall/latency_p50_p95/missing_rate/duplicate_rate`。
- CLI `smartalpha research benchmark`，结论：Helius 保持 Primary，不替换。

## 7. Research Memory / Hypothesis
- Schema (必含)：
  ```yaml
  name: str
  thesis: str
  features: list[str]  # 每个 feature 注明可用时间点 t0+...
  entry_rule: str
  exit_rule: str
  expected_edge: str
  falsification_condition: str  # 无此字段拒绝落盘
  known_biases: list[str]
  ```
- `data/research/memory.json`: 已验证/已证伪 hypothesis 列表，用于去重。
- 生成器：基于 memory + funder_score / holder_concentration / latency 等特征模板，避免重复已 falsified。

## 8. Runner / Reviewer / Red Team
- `research/runner.py: run_historical/run_oos/run_robustness` 统一调度，Discovery/Evaluation 窗口分离，滑点/手续费/Latency 纳入。
- Reviewer 检查：feature `observed_at <= signal_ts`、窗口分离、指标计算、hypothesis↔experiment 一致性。
- Red Team 攻击矩阵：survivorship/selection/tiny-N/repeated wallets/dup families/regime/latency/slippage/stale liq/label drift/overfit。
- 未通过则阻断晋级，报告落 `data/research/reviews/<hypo>.json` / `redteam/<hypo>.json`。

## 9. Leaderboard & CLI
- `smartalpha research cycle [--dry-run]` 串联全链路，产 `data/research/leaderboard.json` 按 `oos_net_expectancy` 排序。
- 旧命令 `prove/backtest-funders/walk-forward/watch-launches/paper-log/gmgn-cookie(弃用)` 保持 `--help` 兼容；`gmgn-cookie` 标记弃用。
- 晋级门禁：
  - PROMISING: OOS signals≥10, best_net>0, win_rate≥35%或net>1SOL
  - PROVEN: 上述 + paper strict≥30, 300s net EV>0, 延迟快照≥5
  - FALSIFIED: 足样本且 EV≤0

## 10. 落盘结构
```
data/research/
  memory.json
  leaderboard.json
  hypotheses/<name>.json
  runs/<id>/report.json
  reviews/<name>.json
  redteam/<name>.json
  snapshots/<mint>.json
  benchmark_gmgn_vs_helius.json
  kline_cache/<mint>_{30s|1m}.json (可选)
```

## 11. 验证清单 (对齐契约)
- `research cycle --dry-run` 产 memory+leaderboard
- `rg gmgn_cookie` 零命中，providers/gmgn.py 存在
- Kline 替代 proxy，TP/SL 单测过
- 所有产出含 source/observed_at
- hypothesis 含 falsification_condition，review/redteam 报告存在
- `self-check/pytest/ruff` 全绿，旧命令兼容
- benchmark 四指标产出，Helius 为主

## 12. 风险与取舍 (ponytail: 显式记录)
- ponytail: GMGN 官方 Kline 若部分老 mint 无 30s 粒度，降级到 1m，再降级到 DexScreener fallback，记录 `kline_fallback` 标记。
- ponytail: 无 Key 环境 `research cycle` 仅 `--dry-run` fixture 路径可跑，真实调用需 GMGN_API_KEY/HELIUS_API_KEY。
- ponytail: Snapshot 定时用 asyncio sleep 调度，若 V1 先以同步顺序拉取+时间戳校验实现，后续再换真定时。
