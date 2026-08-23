# GOAL V3 — Automated Hypothesis Discovery

> **前置**：`GOAL V2` 与 `Research V1 evidence-real` 已于 `d1673eef` 冻结，底层 temporal invariants、outcome-blind universe、单一 ledger split、60m `label_c.time` 精确 gate 均不再改动。V3 在此基座上构建自动发现能力。

## Objective

在已冻结的 V2 基座上交付 **Automated Hypothesis Discovery** 首个闭环：

1. **Research Memory 持久化账本**  
   Append-only `data/research/memory_ledger.jsonl`（或 SQLite），记录 `hypothesis_id / DSL / lineage / OOS 结果 / verdict`，支持按 `PROMISING / FALSIFIED / INSUFFICIENT` 查询与去重，重启不丢。

2. **Hypothesis DSL + 编译器**  
   提供 JSON Schema（`src/smartalpha/research/hypothesis_dsl.json`）、≥5 结构化示例（`examples/hypotheses/*.json`）、确定性 `DSL → Experiment` 编译器（`src/smartalpha/research/dsl_compiler.py`），错误 DSL 明确拒绝。

3. **自动发现雏形 → 全链路**  
   基于 Memory 与 DSL 的规则/模板生成器可产出≥1 新假设，并走通 `Memory → DSL → Compiler → OOS Engine（现有 V2） → Reviewer/RedTeam → Paper` 全链路，`research cycle --dry-run` 可验证。

**边界**：不改 V2 `seen_mints` 宇宙、60m 标签、`as_of`/`label_c.time` 等 invariants；不引入实盘自动交易；复用现有 OOS/Reviewer 管线。

## Verification Contract

- `ls data/research/memory_ledger.jsonl` 或 `src/smartalpha/research/memory.py` 含 append-only 账本且 `rg -n "hypothesis_id.*verdict" src/smartalpha/research/memory.py` 命中，`uv run pytest -q -k test_memory_ledger` 通过
- `ls src/smartalpha/research/hypothesis_dsl.json` 存在且 `rg -n "DSL.*Schema|hypothesis_dsl" src` 命中，`uv run pytest -q -k test_dsl_compiler` 通过（5 示例编译成功，错误 DSL 拒绝）
- `uv run smartalpha research cycle --dry-run` 可通过 DSL 生成至少 1 新假设并完成 OOS（`selected_mints`/`priced`/`EV` 落盘）
- `uv run ruff check src tests && uv run pytest -q` 全绿

## Notes

- V2 冻结文件：`src/smartalpha/walk_forward.py`（`label_available_at = label_c.time`）、`src/smartalpha/research/experiments.py`（`seen_mints` 单一切分）、`src/smartalpha/launch_intel.py`（`window_sigs` 全量）、`src/smartalpha/research/universe.py`
- 远端 CI：`52 passed` 为本地声明，V3 应补 `GitHub Actions` 独立校验
- 下一步：`Memory → Generator → DSL → Compiler → OOS` 的首个自动 `PROMISING` 假设
