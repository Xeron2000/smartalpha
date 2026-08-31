"""Fail-closed execution orchestration for an external Pump signer.

This process owns strategy, risk, idempotency, and position state. The signer
owns protocol-specific transaction construction, signing, and broadcast using
the official Pump/PumpSwap SDK/IDL. No private key is read here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import httpx

from smartalpha.config import ROOT, Settings
from smartalpha.db import Store
from smartalpha.exit_rules import ExitPolicy, exit_action, exit_policy


class ExecutionMode(StrEnum):
    PAPER = "paper"
    SHADOW = "shadow"
    CANARY = "canary"


@dataclass(frozen=True)
class ExecutionResult:
    idempotency_key: str
    mode: str
    status: str
    reason: str = ""
    tx_signature: str | None = None
    position_opened: bool = False


class SignerClient:
    """HTTP boundary for an isolated signer/quote service."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(timeout=timeout)

    def quote(self, intent: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/quote", intent)

    def execute(self, intent: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/execute", intent)

    def status(self, idempotency_key: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/status/{idempotency_key}")

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        kwargs: dict[str, Any] = {"headers": headers}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            kwargs["json"] = payload
        response = self._client.request(method, self.base_url + path, **kwargs)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("signer response must be a JSON object")
        return body


class ExecutionEngine:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        store: Store | None = None,
        signer: SignerClient | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.store = store or Store(self.settings.db_path)
        self.exit_policy: ExitPolicy = exit_policy(self.settings)
        self.signer = signer or (
            SignerClient(
                self.settings.execution_signer_url,
                self.settings.execution_signer_token,
            )
            if self.settings.execution_signer_url
            else None
        )

    def reconcile_pending(self) -> None:
        """Resolve submitted signer calls before accepting new risk."""
        if not self.signer:
            return
        for order in self.store.list_execution_orders():
            if order["side"] != "entry" or order["status"] not in {"submitted", "needs_reconcile"}:
                continue
            try:
                response = self.signer.status(order["idempotency_key"])
            except Exception as exc:
                self.store.update_execution_order(order["idempotency_key"], error=f"reconcile: {exc}")
                continue
            if str(response.get("status", "")).lower() in {"failed", "rejected", "expired"}:
                self.store.update_execution_order(
                    order["idempotency_key"],
                    status="failed",
                    response_json=json.dumps(response, ensure_ascii=False),
                    error=str(response.get("error") or response.get("status")),
                )
                continue
            self._record_entry_fill(order, response)

    def submit_entry(self, signal: Any) -> ExecutionResult:
        """Submit one idempotent entry intent, or record why it was rejected."""
        key = _idempotency_key("entry", signal.mint, signal.ts)
        existing = self.store.get_execution_order(key)
        if existing and existing["status"] in {"submitted", "needs_reconcile"}:
            if self.signer:
                try:
                    recovered = self._record_entry_fill(
                        existing,
                        self.signer.status(key),
                        signal=signal,
                    )
                    if recovered:
                        return recovered
                except Exception:
                    pass
            existing = self.store.get_execution_order(key) or existing
        if existing and existing["status"] in {
            "paper",
            "shadow",
            "quoted",
            "confirmed",
            "failed",
            "rejected",
        }:
            return _result_from_order(existing)

        mode = _parse_mode(self.settings.execution_mode)
        slippage_bps = _slippage_bps(self.settings.execution_max_slippage_pct)
        reason = self._entry_rejection(signal, mode, slippage_bps)
        if reason:
            self._create_or_retry_order(
                key,
                mint=signal.mint,
                side="entry",
                mode=mode.value if mode else self.settings.execution_mode,
                status="rejected",
                signal_ts=int(signal.ts),
                amount_sol=None,
                token_amount=None,
                slippage_bps=slippage_bps,
                error=reason,
            )
            return ExecutionResult(key, mode.value if mode else "invalid", "rejected", reason)

        amount_sol = float(self.settings.execution_trade_size_sol)
        self._create_or_retry_order(
            key,
            mint=signal.mint,
            side="entry",
            mode=mode.value,
            status="submitted" if mode == ExecutionMode.CANARY else mode.value,
            signal_ts=int(signal.ts),
            amount_sol=amount_sol,
            token_amount=None,
            slippage_bps=slippage_bps,
        )
        if mode == ExecutionMode.PAPER:
            return ExecutionResult(key, mode.value, "paper", "no order broadcast")

        intent = self._entry_intent(signal, key, slippage_bps, amount_sol)
        if mode == ExecutionMode.SHADOW:
            if not self.signer:
                return ExecutionResult(key, mode.value, "shadow", "signer not configured")
            try:
                quote = self.signer.quote(intent)
                self._save_paper_quote(signal.mint, "0", quote)
            except Exception as exc:
                self.store.update_execution_order(key, status="shadow", error=f"quote: {exc}")
                return ExecutionResult(key, mode.value, "shadow", f"quote failed: {exc}")
            self.store.update_execution_order(
                key,
                status="quoted",
                response_json=json.dumps(quote, ensure_ascii=False),
            )
            return ExecutionResult(key, mode.value, "quoted", "quote received")

        try:
            quote = self.signer.quote(intent) if self.signer else None
            if quote is None:
                raise RuntimeError("signer not configured")
            self._save_paper_quote(signal.mint, "0", quote)
            intent["quote"] = quote
            response = self.signer.execute(intent) if self.signer else None
            if response is None:
                raise RuntimeError("signer not configured")
        except Exception as exc:
            self.store.update_execution_order(key, status="failed", error=f"execute: {exc}")
            return ExecutionResult(key, mode.value, "failed", str(exc))

        result = self._record_entry_fill(
            self.store.get_execution_order(key) or {
                "idempotency_key": key,
                "mint": signal.mint,
                "mode": mode.value,
                "signal_ts": signal.ts,
            },
            response,
            signal=signal,
        )
        if result:
            return result
        tx_signature = _text(response, "tx_signature", "signature")
        self.store.update_execution_order(
            key,
            status="needs_reconcile",
            response_json=json.dumps(response, ensure_ascii=False),
            tx_signature=tx_signature,
            error="signer response lacks filled amounts; do not retry blindly",
        )
        return ExecutionResult(key, mode.value, "needs_reconcile", "filled amounts missing", tx_signature)

    def _record_entry_fill(
        self,
        order: dict,
        response: dict[str, Any],
        *,
        signal: Any | None = None,
    ) -> ExecutionResult | None:
        tx_signature = _text(response, "tx_signature", "signature")
        token_amount = _number(response, "filled_base_amount", "token_amount", "base_amount")
        spent_sol = _number(response, "spent_quote_sol", "spent_sol", "amount_sol")
        entry_price = _number(response, "entry_price_usd", "price_usd")
        if not tx_signature or token_amount is None or spent_sol is None or token_amount <= 0 or spent_sol <= 0:
            return None
        self.store.update_execution_order(
            order["idempotency_key"],
            status="confirmed",
            response_json=json.dumps(response, ensure_ascii=False),
            tx_signature=tx_signature,
            token_amount=token_amount,
            error=None,
        )
        signal_price = getattr(signal, "price_usd", None) if signal is not None else None
        self.store.upsert_position(
            mint=order["mint"],
            status="open",
            token_amount=token_amount,
            entry_sol=spent_sol,
            entry_price_usd=entry_price or signal_price,
            opened_ts=int(order["signal_ts"]),
        )
        return ExecutionResult(
            order["idempotency_key"],
            order["mode"],
            "confirmed",
            tx_signature=tx_signature,
            position_opened=True,
        )

    def paper_snapshot_provider(self, signal: Any):
        if not self.signer or _parse_mode(self.settings.execution_mode) == ExecutionMode.PAPER:
            return None

        def fetch(delay: int) -> dict[str, Any] | None:
            amount_sol = float(self.settings.execution_trade_size_sol)
            if amount_sol <= 0:
                return None
            intent = self._entry_intent(
                signal,
                _idempotency_key("paper", signal.mint, delay),
                _slippage_bps(self.settings.execution_max_slippage_pct),
                amount_sol,
            )
            intent["purpose"] = "paper_mark"
            intent["delay_sec"] = delay
            try:
                quote = self.signer.quote(intent)
            except Exception:
                return None
            return _quote_snapshot(quote)

        return fetch

    def _save_paper_quote(self, mint: str, key: str, quote: dict[str, Any]) -> None:
        snapshot = _quote_snapshot(quote)
        self.store.merge_paper_snapshot(
            mint,
            key,
            snapshot,
            price_usd=_number(snapshot, "price_usd"),
        )

    async def monitor_position(self, mint: str) -> None:
        """Manage a canary position from fresh executable signer quotes."""
        if self.signer is None:
            return
        while True:
            position = self.store.get_position(mint)
            if not position or position["status"] != "open":
                return
            try:
                quote = await asyncio.to_thread(self.signer.quote, self._exit_intent(position))
                output_sol = _number(quote, "output_sol", "quote_out_sol", "received_quote_sol")
                observed_at = int(_number(quote, "observed_at") or time.time())
                if output_sol is None or time.time() - observed_at > self.settings.execution_quote_ttl_sec:
                    await asyncio.sleep(max(1, self.settings.execution_poll_sec))
                    continue
            except Exception:
                await asyncio.sleep(max(1, self.settings.execution_poll_sec))
                continue

            entry_sol = float(position["entry_sol"])
            return_pct = (output_sol / entry_sol - 1.0) * 100.0 if entry_sol > 0 else -100.0
            peak = max(float(position["peak_return_pct"] or 0.0), return_pct)
            self.store.update_position(
                mint,
                peak_return_pct=peak,
                last_quote_ts=observed_at,
            )
            action = self._exit_action(position, return_pct, peak)
            if action:
                await asyncio.to_thread(self._submit_exit, position, action, quote)
            await asyncio.sleep(max(1, self.settings.execution_poll_sec))

    def _entry_rejection(self, signal: Any, mode: ExecutionMode | None, slippage_bps: int) -> str | None:
        if mode is None:
            return f"invalid execution mode: {self.settings.execution_mode}"
        if not getattr(signal, "strict_entry", False):
            return "signal is not strict"
        if getattr(signal, "copytrap_risk", "high") == "high":
            return "copytrap risk is high"
        if mode != ExecutionMode.CANARY:
            return None
        if not self.settings.execution_canary_armed:
            return "canary is not armed"
        if not self.signer:
            return "external signer is not configured"
        if self.settings.execution_signer_url and not _safe_signer_url(self.settings.execution_signer_url):
            return "external signer must use HTTPS or loopback HTTP"
        if self.settings.execution_require_proven and not self._proof_is_proven():
            return "latest prove report is not PROVEN"
        if self.settings.execution_trade_size_sol <= 0:
            return "EXECUTION_TRADE_SIZE_SOL must be positive"
        if self.settings.execution_max_daily_loss_sol <= 0:
            return "EXECUTION_MAX_DAILY_LOSS_SOL must be positive"
        if slippage_bps <= 0 or slippage_bps > 10_000:
            return "invalid maximum slippage"
        if len(self.store.list_open_positions()) >= self.settings.execution_max_open_positions:
            return "maximum open positions reached"
        if self.store.today_realized_loss_sol() >= self.settings.execution_max_daily_loss_sol:
            return "daily loss circuit breaker is open"
        return None

    def _proof_is_proven(self) -> bool:
        path = self.settings.execution_proof_path
        if not path.is_absolute():
            path = ROOT / path
        try:
            return json.loads(path.read_text()).get("verdict") == "PROVEN"
        except (OSError, TypeError, json.JSONDecodeError):
            return False

    def _intent(
        self,
        *,
        side: str,
        mint: str,
        idempotency_key: str,
        max_slippage_bps: int,
        **fields: Any,
    ) -> dict[str, Any]:
        return {
            "protocol": "smartalpha.execution.v1",
            "idempotency_key": idempotency_key,
            "venue": "pump_fun_or_pumpswap",
            "side": side,
            "mint": mint,
            "max_slippage_bps": max_slippage_bps,
            **fields,
        }

    def _entry_intent(self, signal: Any, key: str, slippage_bps: int, amount_sol: float) -> dict[str, Any]:
        return self._intent(
            side="buy",
            mint=signal.mint,
            idempotency_key=key,
            max_slippage_bps=slippage_bps,
            creator=signal.creator,
            quote_amount_sol=amount_sol,
            signal_ts=int(signal.ts),
            signal_price_usd=signal.price_usd,
            signal_liquidity_usd=signal.liquidity_usd,
        )

    def _exit_intent(self, position: dict) -> dict[str, Any]:
        return self._intent(
            side="sell",
            mint=position["mint"],
            idempotency_key=_idempotency_key(
                "quote-exit", position["mint"], position["updated_ts"]
            ),
            max_slippage_bps=_slippage_bps(self.settings.execution_max_slippage_pct),
            base_amount=float(position["token_amount"]),
        )

    def _exit_action(self, position: dict, return_pct: float, peak_pct: float) -> tuple[str, float] | None:
        return exit_action(
            return_pct,
            peak_pct,
            int(time.time()) - int(position["opened_ts"]),
            tp1_done=bool(position["tp1_done"]),
            tp2_done=bool(position["tp2_done"]),
            policy=self.exit_policy,
        )

    def _submit_exit(
        self,
        position: dict,
        action: tuple[str, float],
        quote: dict[str, Any],
    ) -> None:
        stage, fraction = action
        key = _idempotency_key("exit", position["mint"], stage)
        existing = self.store.get_execution_order(key)
        if existing and existing["status"] in {"submitted", "confirmed", "needs_reconcile"}:
            return
        sell_amount = min(float(position["token_amount"]), float(position["token_amount"]) * fraction)
        if sell_amount <= 0:
            return
        self._create_or_retry_order(
            key,
            mint=position["mint"],
            side="exit",
            mode=ExecutionMode.CANARY.value,
            status="submitted",
            signal_ts=int(position["opened_ts"]),
            amount_sol=None,
            token_amount=sell_amount,
            slippage_bps=_slippage_bps(self.settings.execution_max_slippage_pct),
        )
        intent = self._intent(
            side="sell",
            mint=position["mint"],
            idempotency_key=key,
            max_slippage_bps=_slippage_bps(self.settings.execution_max_slippage_pct),
            base_amount=sell_amount,
            quote=quote,
            reason=stage,
        )
        try:
            response = self.signer.execute(intent) if self.signer else None
            if response is None:
                raise RuntimeError("signer not configured")
        except Exception as exc:
            self.store.update_execution_order(key, status="failed", error=f"execute: {exc}")
            return

        tx_signature = _text(response, "tx_signature", "signature")
        filled = _number(response, "filled_base_amount", "token_amount", "base_amount")
        received = _number(response, "received_quote_sol", "output_sol", "received_sol")
        if not tx_signature or filled is None or received is None:
            self.store.update_execution_order(
                key,
                status="needs_reconcile",
                tx_signature=tx_signature,
                response_json=json.dumps(response, ensure_ascii=False),
                error="exit fill missing; position frozen for reconciliation",
            )
            return

        pnl = received - float(position["entry_sol"]) * min(filled / float(position["token_amount"]), 1.0)
        self.store.update_execution_order(
            key,
            status="confirmed",
            tx_signature=tx_signature,
            token_amount=filled,
            realized_pnl_sol=pnl,
            response_json=json.dumps(response, ensure_ascii=False),
        )
        remaining = max(0.0, float(position["token_amount"]) - filled)
        remaining_basis = max(0.0, float(position["entry_sol"]) - float(position["entry_sol"]) * min(filled / float(position["token_amount"]), 1.0))
        self.store.update_position(
            position["mint"],
            status="closed" if remaining <= 1e-12 else "open",
            token_amount=remaining,
            entry_sol=remaining_basis,
            tp1_done=bool(position["tp1_done"] or stage == "tp1"),
            tp2_done=bool(position["tp2_done"] or stage == "tp2"),
        )

    def _create_or_retry_order(self, key: str, **kwargs: Any) -> None:
        if self.store.create_execution_order(idempotency_key=key, **kwargs):
            return
        current = self.store.get_execution_order(key)
        if current and current["status"] in {"failed", "rejected"}:
            self.store.update_execution_order(key, status=kwargs["status"], error=None)


def _safe_signer_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )


def _parse_mode(value: str) -> ExecutionMode | None:
    try:
        return ExecutionMode(value.lower())
    except (AttributeError, ValueError):
        return None


def _slippage_bps(percent: float) -> int:
    return int(round(max(0.0, percent) * 100.0))


def _idempotency_key(kind: str, mint: str, value: object) -> str:
    raw = f"{kind}:{mint}:{value}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _quote_snapshot(quote: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(quote)
    snapshot.setdefault("source", "signer_quote")
    snapshot.setdefault("observed_at", int(time.time()))
    return snapshot


def _text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    return None


def _number(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _result_from_order(order: dict) -> ExecutionResult:
    return ExecutionResult(
        idempotency_key=order["idempotency_key"],
        mode=order["mode"],
        status=order["status"],
        reason=order.get("error") or "",
        tx_signature=order.get("tx_signature"),
        position_opened=order["status"] == "confirmed" and order["side"] == "entry",
    )
