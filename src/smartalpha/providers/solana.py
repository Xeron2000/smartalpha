from __future__ import annotations

import time
from typing import Any

from smartalpha.rpc import SolanaRpc


def envelope(data: Any, source: str = "solana") -> dict:
    return {"data": data, "source": source, "observed_at": int(time.time())}


def get_signatures_with_meta(rpc: SolanaRpc, address: str, **kw) -> dict:
    sigs = rpc.get_signatures(address, **kw)
    return envelope(sigs, "solana")


def get_transaction_with_meta(rpc: SolanaRpc, sig: str) -> dict:
    tx = rpc.get_transaction(sig)
    return envelope(tx, "solana")
