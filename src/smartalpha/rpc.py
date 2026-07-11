from __future__ import annotations

import time
from typing import Any

import httpx

from smartalpha.types import Side, TradeEvent

WSOL = "So11111111111111111111111111111111111111112"


class SolanaRpc:
    def __init__(self, url: str) -> None:
        self.url = url
        self._id = 0
        # ponytail: one client per instance, reused across calls
        self._client = httpx.Client(timeout=30.0)

    def _call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        r = self._client.post(self.url, json=body)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        return data["result"]

    def get_signatures(
        self, address: str, *, before: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        opts: dict[str, Any] = {"limit": limit}
        if before:
            opts["before"] = before
        result = self._call("getSignaturesForAddress", [address, opts])
        return result or []

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = self._call(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        return result


def parse_wallet_swaps(tx: dict[str, Any] | None, wallet: str) -> list[TradeEvent]:
    """Extract token balance changes for wallet from parsed transaction."""
    if not tx or tx.get("meta", {}).get("err"):
        return []

    meta = tx["meta"]
    block_time = int(tx.get("blockTime") or time.time())
    signature = tx["transaction"]["signatures"][0]

    pre = _balances_by_mint(meta.get("preTokenBalances") or [], wallet)
    post = _balances_by_mint(meta.get("postTokenBalances") or [], wallet)
    mints = set(pre) | set(post)

    pre_lamports = _wallet_lamports(meta.get("preBalances") or [], tx, wallet)
    post_lamports = _wallet_lamports(meta.get("postBalances") or [], tx, wallet)
    sol_delta = (post_lamports - pre_lamports) / 1e9

    events: list[TradeEvent] = []
    for mint in mints:
        if mint == WSOL:
            continue
        delta = post.get(mint, 0.0) - pre.get(mint, 0.0)
        if abs(delta) < 1e-12:
            continue
        side = Side.BUY if delta > 0 else Side.SELL
        events.append(
            TradeEvent(
                wallet=wallet,
                mint=mint,
                side=side,
                sol_delta=abs(sol_delta) if side == Side.BUY else -abs(sol_delta),
                token_delta=delta,
                signature=signature,
                ts=block_time,
            )
        )
    return events


def _balances_by_mint(balances: list[dict], wallet: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for b in balances:
        if b.get("owner") != wallet:
            continue
        mint = b.get("mint")
        if not mint:
            continue
        ui = b.get("uiTokenAmount") or {}
        amt = float(ui.get("uiAmount") or 0)
        out[mint] = out.get(mint, 0.0) + amt
    return out


def _wallet_lamports(balances: list[int], tx: dict, wallet: str) -> int:
    keys = tx["transaction"]["message"]["accountKeys"]
    addrs = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    try:
        idx = addrs.index(wallet)
        return balances[idx]
    except (ValueError, IndexError):
        return 0
