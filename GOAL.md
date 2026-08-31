# SmartAlpha Research Goal

> **Mission:** Build a machine that continuously measures, tests, falsifies, and proves Solana early-launch alpha using first-principles market microstructure.
>
> SmartAlpha is not trying to produce more strategies. It is trying to produce **fewer false beliefs** and execute strictly on strategies that survive realistic slippage, price impact, and paper validation.

Last updated: 2026-08-31

---

## 1. North Star & Production Thesis

> **Active Strategy Spec:** [`docs/STRATEGY_SPEC.md`](docs/STRATEGY_SPEC.md)
> **Proof Protocol & Gates:** [`docs/PROOF_PROTOCOL.md`](docs/PROOF_PROTOCOL.md)

### Core Question
> **Can on-chain liquidity depth, unique buyer entropy, and turnover velocity predict a tradable positive-EV entry after price impact, DEX fees, and Jito/Gas friction?**

### Empirical Status
1. **FALSIFIED**: Blind sniping without liquidity/entropy filters (Net EV: -17.77% @ 5m, -2.83% @ 1h).
2. **FALSIFIED**: Pure funder/smart-money parasite tracking (prone to sybil wash trading, copytraps, and negative OOS EV).
3. **PROVEN (Active)**: First-Principles Four Pillars (Reserve $\ge \$3,000$, Unique Buyers $\ge 8$, $V/\text{Reserve} \ge 0.5$, Sizing \$100-\$250) producing net positive EV (+45% ~ +127%) with 0% instant rug rate.

---

## 2. Research Scope: Stay Narrow

SmartAlpha focuses strictly on **Solana early-launch microstructural alpha**:

1. **Liquidity Guard**: Reserve depth vs trade impact $\Delta P \approx \frac{S}{2L}$;
2. **Orderflow Entropy**: Unique buyer dispersion vs single-wallet wash trading;
3. **Turnover Velocity**: 180s/1h volume vs reserve ratio for escape velocity;
4. **Friction Damping**: Dynamic price impact, DEX fee, and Gas deduction;
5. **Execution Horizon**: Optimal 15m–60m laddered take-profit and momentum exits.

Out of scope for V1 unless a result above creates a concrete reason to add them:

- generic TA indicator search;
- social-sentiment LLM scoring;
- news prediction;
- multi-chain expansion;
- full autonomous execution;
- hundreds of unrelated strategy families.

**Rule:** go deeper before going wider.

---

## 3. Research Invariants

These rules override convenience and model suggestions.

### 3.1 No look-ahead

A feature used at signal time must have been observable at or before the signal timestamp.

Do not use current-state fields as historical truth unless the dataset explicitly snapshots them at that time.

Examples:

- current liquidity is not launch-time liquidity;
- current holder concentration is not launch-time holder concentration;
- a wallet tag created later must not silently appear in an earlier backtest;
- current h24 PnL is not the PnL known at entry time.

### 3.2 Discovery and evaluation must be separated

The same data cannot be freely used both to discover a wallet/funder and to claim that wallet/funder predicts future returns.

At minimum:

```text
older window → discover / train
newer window → evaluate OOS
future live events → paper validate
```

### 3.3 Every hypothesis must be falsifiable

Every generated hypothesis must specify:

```yaml
name: string
thesis: why the edge may exist
features: observable inputs
entry_rule: deterministic rule
exit_rule: deterministic rule
expected_edge: what should improve
falsification_condition: result that kills the idea
known_biases: likely sources of false alpha
```

A hypothesis without a falsification condition is not an experiment.

### 3.4 Reproducibility over narrative

Every experiment must save:

- dataset/version or source timestamps;
- code/strategy version;
- parameters;
- signal count;
- train/OOS split;
- execution assumptions;
- metrics;
- failure reason or promotion reason.

An LLM explanation is commentary, not evidence.

### 3.5 Paper before money

Historical evidence can promote a strategy only to **PROMISING**.

Only live paper evidence can promote it to **PROVEN**.

PROVEN still means small capital + hard kill switches. It does not mean unlimited sizing.

---

## 4. Research Agent Roles

Use abundant model tokens to create disagreement, not just more code.

### Hypothesis Agent

Reads research memory and proposes new falsifiable hypotheses.

It must explain how each idea differs from already failed experiments.

### Builder Agent

Implements the experiment behind a stable experiment interface.

It must not change production signal behavior while implementing research code.

### Runner

Runs historical, OOS, robustness, and execution-cost tests and writes machine-readable artifacts.

### Reviewer Agent

Checks implementation correctness, feature timestamp validity, metric validity, and whether the experiment actually tests the stated hypothesis.

### Red-Team Agent

Its goal is to **kill the strategy**.

It should actively look for:

- look-ahead bias;
- survivorship bias;
- selection bias;
- data leakage;
- repeated-wallet dependence;
- duplicated mint families;
- parameter overfitting;
- tiny sample size;
- regime dependence;
- unrealistic fills;
- missing latency;
- stale liquidity;
- API fields that were not available historically;
- provider-specific labels that may change retrospectively.

### Judge

Promotes only strategies that pass deterministic gates. The Judge must not promote an experiment because an LLM says it "looks strong".

### Research Memory

Stores negative results as first-class assets.

The system should prefer a new hypothesis over repeating a failed one unless there is a clearly stated new variable, dataset, or causal mechanism.

---

## 5. Experiment Contract

All generated strategies should eventually conform to a common experiment contract rather than writing one-off backtest scripts.

Minimum result schema:

```json
{
  "experiment": "funder_cluster_recency_v1",
  "signals_train": 0,
  "signals_oos": 0,
  "win_rate_oos": null,
  "net_expectancy_oos": null,
  "max_drawdown_oos": null,
  "latency_assumption_ms": null,
  "slippage_pct": null,
  "parameter_stability": null,
  "paper_signals": 0,
  "paper_net_expectancy_300s": null,
  "verdict": "INSUFFICIENT_DATA"
}
```

The exact schema may evolve, but every experiment must be comparable on one leaderboard.

---

## 6. Promotion Gates

The existing `docs/PROOF_PROTOCOL.md` remains the execution-level source of truth. Research automation should make those gates stricter over time, not bypass them.

### Stage 0 — Data validity

Reject before backtesting if:

- required timestamps are unavailable;
- historical feature values cannot be reconstructed;
- outcome data is obviously stale/current-state leakage;
- the dataset is dominated by already-known winners.

### Stage 1 — Historical exploration

Purpose: decide whether the hypothesis deserves OOS evaluation.

No production conclusions are allowed here.

### Stage 2 — Walk-forward OOS

Minimum requirement: enough independent OOS signals to make the result meaningful.

Do not promote tiny-N experiments because a few trades produced large PnL.

### Stage 3 — Robustness

A candidate should survive reasonable changes to:

- entry threshold;
- exit rule;
- latency;
- slippage;
- time split;
- position sizing;
- exclusion of the best few trades.

If the edge disappears after a tiny parameter change, treat it as overfit.

### Stage 4 — Live paper

Paper snapshots are the decisive test because they measure the actual information and execution timing available to the live system.

Main window remains 300s unless later evidence justifies another primary horizon.

### Verdicts

- **PROVEN** — OOS + robustness + paper all pass.
- **PROMISING** — OOS/robustness pass; paper incomplete.
- **INSUFFICIENT_DATA** — cannot make a reliable claim.
- **FALSIFIED** — enough evidence shows no usable positive edge.

---

## 7. Data-Source Strategy

### Principle

Do not force one provider to do every job.

Use the provider that best preserves the truth required by that research layer.

### GMGN OpenAPI — primary research / enrichment layer

Use the **official GMGN OpenAPI / gmgn-cli interfaces**, not browser-cookie scraping.

Preferred uses:

- candidate discovery / Trenches;
- trending and token signals;
- 30s/1m+ K-line outcomes;
- realtime token price, liquidity, market cap;
- token security fields;
- pool information;
- holders and top traders;
- wallet holdings / activity / stats;
- creator token history;
- GMGN wallet labels such as smart money, sniper, bundler, insider-like tags;
- Smart Money / KOL activity.

GMGN is especially valuable because it provides normalized and labeled intelligence that would be expensive to reconstruct from raw Solana history.

**Important:** GMGN labels are features, not ground truth. Record the observation timestamp because platform labels/derived metrics can change later.

### Helius WebSocket — primary live launch detector

Keep Helius (or an equivalent low-latency Solana websocket/Geyser source) for raw launch observation.

Current `logsSubscribe` detection gives SmartAlpha an event-driven t0. Do **not** replace it with periodic GMGN Trenches polling merely to reduce the number of providers.

GMGN polling can be used as:

- gap recovery;
- secondary discovery;
- cross-checking missed launches;
- enrichment after t0.

It should not become the sole source of live launch timestamps until measured evidence proves equal or better capture latency and completeness.

### Solana RPC — raw fact / graph layer

Keep RPC access for information that must be reconstructed from raw transactions, including funding provenance and transfer relationships when no equivalent historical GMGN field exists.

The raw chain is the final verification source when an aggregated provider and the chain disagree.

### DexScreener / GeckoTerminal — secondary validation / fallback

Do not use them as the primary research outcome source when GMGN 30s/1m K-lines are available.

They remain useful for:

- provider cross-checks;
- outage fallback;
- discovering provider disagreements.

---

## 8. Immediate Data Migration Priorities

### P0 — remove brittle GMGN browser-cookie dependency

Current code calls undocumented `gmgn.ai/defi/...` endpoints with browser headers, cookies, Cloudflare state, and `curl`.

Replace this path with official `GMGN_API_KEY`-based OpenAPI access.

Target:

```text
mint_sources.py
GMGN cookie scraping
        ↓
GMGN official OpenAPI provider
```

Do not delete DexScreener/Gecko fallbacks until parity is measured.

### P0 — replace coarse historical outcome proxies

Where current backtests use DexScreener rolling h1/h6/h24 values as proxies, prefer GMGN historical K-line data.

Use 30s/1m candles where available to reconstruct:

- entry price near signal time;
- max favorable/adverse excursion;
- TP/SL ordering;
- 90s / 180s / 300s / 900s outcomes;
- latency sensitivity.

This migration is more valuable than replacing the launch websocket because it directly reduces backtest measurement error.

### P1 — GMGN enrichment snapshots

At live launch t0, keep raw chain detection, then snapshot GMGN-derived features at controlled delays, for example:

```text
t0        raw launch observed
+t10s     token / pool / security snapshot
+t30s     holders / traders / smart-money snapshot
+t90s     final entry-feature snapshot
+t300s    outcome snapshot
```

Actual delays should be measured and adjusted based on API availability and rate limits.

### P1 — provider abstraction

Create provider boundaries so strategy code does not depend on one vendor:

```text
providers/
  gmgn.py
  solana.py
  dexscreener.py
```

Research code consumes normalized records with `source` and `observed_at` fields.

### P2 — GMGN gap detector

Periodically compare Helius-observed Pump launches with GMGN `trenches new_creation` results.

Measure:

- capture recall;
- t0 latency difference;
- missing-token rate;
- false/duplicate rate.

Only after this benchmark may the project reconsider which source owns launch detection.

---

## 9. Rate-Limit and Data Integrity Rules

GMGN OpenAPI is rate limited. Research throughput must respect endpoint weights and cache aggressively.

Rules:

1. never spam retry on 429;
2. honor reset timestamps;
3. cache immutable/historical responses;
4. batch wallet queries where supported;
5. store raw provider response or a content hash for important experiment inputs;
6. every normalized record includes `source` and `observed_at`;
7. never silently substitute another provider without recording it;
8. provider disagreement should become a metric, not be hidden.

A fast experiment built on ambiguous data is worse than a slower reproducible one.

---

## 10. Research Memory Layout

Target structure:

```text
research/
  hypotheses/
  experiments/
  rejected/
  promising/
  proven/
  memory.jsonl
```

Each completed experiment should append a compact memory record containing:

- hypothesis;
- result;
- failure/promotion reason;
- important bias discovered;
- reusable feature/data lesson;
- next experiment only if materially different.

Negative results must remain searchable.

---

## 11. V1 Deliverable

The first Research Factory milestone is complete when this command (or an equivalent single entry point) can run end to end:

```bash
uv run smartalpha research cycle
```

Expected behavior:

```text
load research memory
→ generate bounded hypotheses
→ validate experiment schema
→ implement/run isolated experiments
→ historical screening
→ walk-forward OOS
→ robustness tests
→ reviewer
→ red team
→ leaderboard
→ persist results
```

V1 must **not**:

- modify live production rules automatically;
- merge generated code automatically;
- place real trades;
- promote tiny-sample results;
- treat GMGN labels as immutable historical truth.

Human review remains the final gate between research and production behavior.

---

## 12. Decision Rule for Future Work

Before adding a feature, data provider, agent, or strategy, ask:

> **Will this materially improve our ability to discover, falsify, measure, or reproduce alpha?**

If not, do not add it.

Before opening a new strategy family, ask:

> **Has the current wallet/funder thesis been adequately proven or falsified?**

If not, keep digging.

The scarce resource is no longer model tokens.

The scarce resources are **clean evidence, independent samples, live time, and focus**.
