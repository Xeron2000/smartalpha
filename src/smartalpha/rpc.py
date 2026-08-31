from __future__ import annotations

from typing import Any

import httpx

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
        response = self._client.post(self.url, json=body)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        return data["result"]

    def get_signatures(
        self, address: str, *, before: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        opts: dict[str, Any] = {"limit": limit}
        if before:
            opts["before"] = before
        return self._call("getSignaturesForAddress", [address, opts]) or []

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        return self._call(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
