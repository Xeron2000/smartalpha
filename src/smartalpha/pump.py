from __future__ import annotations

from smartalpha.rpc import WSOL

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
CREATE_LOG_MARKERS = ("Instruction: Create", "Program log: Instruction: Create")


def is_pump_create_logs(logs: list[str]) -> bool:
    return any(m in line for line in logs for m in CREATE_LOG_MARKERS)


def parse_pump_create_tx(tx: dict | None) -> tuple[str, str] | None:
    """Return (mint, creator) from a pump.fun Create transaction."""
    if not tx or tx.get("meta", {}).get("err"):
        return None
    logs = tx.get("meta", {}).get("logMessages") or []
    if not is_pump_create_logs(logs):
        return None

    mint: str | None = None
    for b in tx.get("meta", {}).get("postTokenBalances") or []:
        m = b.get("mint")
        if m and m != WSOL:
            mint = m
            break
    if not mint:
        return None

    keys = tx["transaction"]["message"]["accountKeys"]
    creator = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
    return mint, creator
