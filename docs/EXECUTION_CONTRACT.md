# SmartAlpha Execution Contract

`smartalpha` owns signal eligibility, risk limits, idempotency, and position state. A separate signer service owns Pump.fun/PumpSwap transaction construction, signing, and broadcast. The signer must never return a success without filled amounts.

## Modes

- `paper`: record strict signals; no signer call.
- `shadow`: optionally call `/v1/quote`; never broadcast.
- `canary`: call `/v1/execute` only when `EXECUTION_CANARY_ARMED=1` and all limits are positive.

## Endpoints

### `POST /v1/quote`

Request fields include `idempotency_key`, `venue`, `side`, `mint`, `base_amount` or `quote_amount_sol`, and `max_slippage_bps`.

Response:

```json
{
  "output_sol": 0.12,
  "price_usd": 0.000001,
  "liquidity_usd": 5000,
  "source": "signer_quote",
  "observed_at": 1730000000,
  "expires_at": 1730000005,
  "quote_id": "provider-specific-id"
}
```

`output_sol` must be the executable amount after protocol/creator fees and the response must be fresh.

### `POST /v1/execute`

The request is the same intent plus an optional quote. The service must be idempotent by `idempotency_key` and return:

```json
{
  "status": "confirmed",
  "tx_signature": "base58-signature",
  "filled_base_amount": 123.0,
  "spent_quote_sol": 0.1,
  "entry_price_usd": 0.000001
}
```

For sells return `filled_base_amount`, `received_quote_sol`, and `tx_signature`. Missing fill fields are treated as `needs_reconcile`; the bot will not blindly retry.

### `GET /v1/status/{idempotency_key}`

This endpoint must return the same terminal fill fields as `/v1/execute` (or a
terminal `failed`/`rejected` status). The watcher calls it on startup and before
retrying an ambiguous submission.

## Protocol requirements

The signer must use the current official Pump public IDL/SDK, not hard-coded third-party account lists. Pre-graduation trades use Pump bonding-curve instructions with `max_quote_in`/`min_quote_out`; migrated trades use PumpSwap `buy`/`sell` and effective quote reserves (raw quote vault plus `virtual_quote_reserves`). Re-read the protocol state and fees before building each transaction.

The signer must enforce the requested slippage bound, reject expired quotes, use a fresh recent blockhash, and expose confirmation/reconciliation for submitted transactions. Private keys stay outside this repository.
