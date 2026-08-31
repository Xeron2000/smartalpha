# SmartAlpha Goal

## Mission

Build a small, falsifiable Solana launch system that measures early-launch microstructure, rejects weak evidence, and executes only through a fail-closed Paper → Shadow → Canary path.

SmartAlpha optimizes for fewer false beliefs, not more strategies.

## Current scope

- Chain: Solana
- Venues: Pump.fun and PumpSwap only
- Launch detector: Helius WebSocket `logsSubscribe`
- Market facts: Solana RPC plus DexScreener observation snapshots
- Signing/broadcast: external signer service; private keys never enter this repository
- Default mode: Paper
- Sizing: fixed `0.05 SOL` Canary trade, one open position, `0.10 SOL` daily loss cap, maximum 5% slippage
- Kelly sizing: deliberately not used

## Active strategy contract

Entry thresholds and the shared exit policy are maintained only in [`docs/STRATEGY_SPEC.md`](docs/STRATEGY_SPEC.md). Proof requirements are maintained in [`docs/PROOF_PROTOCOL.md`](docs/PROOF_PROTOCOL.md); signer requests and execution safety are maintained in [`docs/EXECUTION_CONTRACT.md`](docs/EXECUTION_CONTRACT.md).

## Evidence gates

`prove` must use a dataset marked `metadata.split=oos` with canonical signal-time fields. It must reproduce the strict entry contract and the shared exit policy after friction.

- Phase 1: at least 10 valid OOS outcomes, positive net EV, and win rate at least 35%.
- Phase 2: at least 30 strict Paper rows with complete t0→300s returns and execution-grade quotes.
- `PROVEN` requires both phases; otherwise Canary remains blocked.

Current local status: `INSUFFICIENT_DATA` because there are no valid OOS or Paper samples.

## Runtime map

| Responsibility | Module |
|---|---|
| CLI | `src/smartalpha/cli.py` |
| Live launch pipeline | `src/smartalpha/launch_watch.py` |
| Chain observation | `src/smartalpha/launch_intel.py`, `src/smartalpha/rpc.py` |
| Signal gates | `src/smartalpha/signal_rules.py` |
| Paper snapshots | `src/smartalpha/paper_log.py` |
| Shared exits | `src/smartalpha/exit_rules.py` |
| Execution state and signer boundary | `src/smartalpha/execution.py` |
| OOS/Paper proof | `src/smartalpha/prove.py` |
| Persistent state | `src/smartalpha/db.py` |

## Operating commands

```bash
uv sync
cp .env.example .env
uv run smartalpha self-check
uv run smartalpha watch-launches
uv run smartalpha paper-log catch-up
uv run smartalpha prove data/oos_candidates.json
```

Do not enable Canary until the external signer contract is implemented, Paper evidence is complete, and `prove` returns `PROVEN`.

## Decision rule

Before adding code, a provider, or a strategy, ask:

> Does it materially improve observable evidence, falsification, reproducibility, or safe execution?

If not, do not add it.
