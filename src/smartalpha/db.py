from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_UNSET = object()


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_mints (
                    mint TEXT PRIMARY KEY,
                    signature TEXT,
                    creator TEXT,
                    status TEXT DEFAULT 'processing',
                    ts INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT NOT NULL UNIQUE,
                    signal_ts INTEGER NOT NULL,
                    creator TEXT,
                    signature TEXT,
                    recommendation TEXT,
                    copytrap_risk TEXT,
                    liquidity_usd REAL,
                    strict_signal INTEGER NOT NULL DEFAULT 0,
                    price_usd REAL,
                    buy_count INTEGER NOT NULL DEFAULT 0,
                    sell_count INTEGER NOT NULL DEFAULT 0,
                    top_buyer_share REAL NOT NULL DEFAULT 0,
                    volume_usd REAL,
                    snapshots_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_paper_signal_ts ON paper_signals(signal_ts);
                CREATE TABLE IF NOT EXISTS execution_orders (
                    idempotency_key TEXT PRIMARY KEY,
                    mint TEXT NOT NULL,
                    side TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    signal_ts INTEGER NOT NULL,
                    amount_sol REAL,
                    token_amount REAL,
                    slippage_bps INTEGER,
                    tx_signature TEXT,
                    realized_pnl_sol REAL,
                    response_json TEXT,
                    error TEXT,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_orders_mint ON execution_orders(mint, created_ts);
                CREATE TABLE IF NOT EXISTS positions (
                    mint TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    token_amount REAL NOT NULL,
                    entry_sol REAL NOT NULL,
                    entry_price_usd REAL,
                    opened_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL,
                    peak_return_pct REAL NOT NULL DEFAULT 0,
                    tp1_done INTEGER NOT NULL DEFAULT 0,
                    tp2_done INTEGER NOT NULL DEFAULT 0,
                    last_quote_ts INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
                """
            )
            self._migrate(c)

    def _migrate(self, c: sqlite3.Connection) -> None:
        """Additive migrations for DBs created before newer columns existed."""
        cols = {
            row[1]
            for row in c.execute("PRAGMA table_info(seen_mints)").fetchall()
        }
        if cols and "status" not in cols:
            c.execute(
                "ALTER TABLE seen_mints ADD COLUMN status TEXT DEFAULT 'done'"
            )
        # paper_signals may predate some columns in old DBs
        if c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_signals'"
        ).fetchone():
            pcols = {
                row[1]
                for row in c.execute("PRAGMA table_info(paper_signals)").fetchall()
            }
            for name, decl in (
                ("strict_signal", "INTEGER NOT NULL DEFAULT 0"),
                ("price_usd", "REAL"),
                ("buy_count", "INTEGER NOT NULL DEFAULT 0"),
                ("sell_count", "INTEGER NOT NULL DEFAULT 0"),
                ("top_buyer_share", "REAL NOT NULL DEFAULT 0"),
                ("volume_usd", "REAL"),
                ("snapshots_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("notes", "TEXT"),
                ("liquidity_usd", "REAL"),
                ("copytrap_risk", "TEXT"),
                ("recommendation", "TEXT"),
                ("signature", "TEXT"),
                ("creator", "TEXT"),
            ):
                if name not in pcols:
                    c.execute(f"ALTER TABLE paper_signals ADD COLUMN {name} {decl}")

    def try_seen_mint(self, mint: str, signature: str, creator: str) -> bool:
        """Atomic: insert if new, return True if this call owns it."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO seen_mints(mint,signature,creator,status,ts) "
                "VALUES (?,?,?,'processing',?)",
                (mint, signature, creator, int(time.time())),
            )
            return cur.rowcount > 0

    def mark_seen_mint_done(self, mint: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE seen_mints SET status='done' WHERE mint=?", (mint,)
            )

    def upsert_paper_signal(
        self,
        *,
        mint: str,
        signal_ts: int,
        creator: str,
        signature: str,
        recommendation: str,
        copytrap_risk: str,
        liquidity_usd: float | None,
        strict_signal: bool,
        price_usd: float | None,
        snapshots: dict[str, dict],
        notes: str = "",
        buy_count: int = 0,
        sell_count: int = 0,
        top_buyer_share: float = 0.0,
        volume_usd: float | None = None,
    ) -> None:
        # enforce Research provenance: missing source/observed_at is an error — never fabricate
        for _k, snap in (snapshots or {}).items():
            if isinstance(snap, dict):
                if not snap.get("source"):
                    raise ValueError(f"snapshot {_k} missing source — provenance required")
                if not snap.get("observed_at"):
                    raise ValueError(f"snapshot {_k} missing observed_at — provenance required")
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO paper_signals(
                    mint, signal_ts, creator, signature, recommendation, copytrap_risk,
                    liquidity_usd, strict_signal,
                    price_usd, buy_count, sell_count, top_buyer_share, volume_usd,
                    snapshots_json, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    signal_ts=excluded.signal_ts,
                    recommendation=excluded.recommendation,
                    copytrap_risk=excluded.copytrap_risk,
                    liquidity_usd=excluded.liquidity_usd,
                    strict_signal=excluded.strict_signal,
                    price_usd=excluded.price_usd,
                    buy_count=excluded.buy_count,
                    sell_count=excluded.sell_count,
                    top_buyer_share=excluded.top_buyer_share,
                    volume_usd=excluded.volume_usd,
                    snapshots_json=excluded.snapshots_json,
                    notes=excluded.notes
                """,
                (
                    mint,
                    signal_ts,
                    creator,
                    signature,
                    recommendation,
                    copytrap_risk,
                    liquidity_usd,
                    1 if strict_signal else 0,
                    price_usd,
                    buy_count,
                    sell_count,
                    top_buyer_share,
                    volume_usd,
                    json.dumps(snapshots),
                    notes,
                ),
            )

    def merge_paper_snapshot(
        self, mint: str, key: str, snapshot: dict, *, price_usd: float | None = None
    ) -> None:
        row = self.get_paper_signal(mint)
        if not row:
            return
        snapshots = json.loads(row.get("snapshots_json") or "{}")
        snapshots[key] = snapshot
        self.upsert_paper_signal(
            mint=mint,
            signal_ts=int(row["signal_ts"]),
            creator=row.get("creator") or "",
            signature=row.get("signature") or "",
            recommendation=row.get("recommendation") or "",
            copytrap_risk=row.get("copytrap_risk") or "",
            liquidity_usd=row.get("liquidity_usd"),
            strict_signal=bool(row.get("strict_signal")),
            price_usd=price_usd if price_usd is not None else row.get("price_usd"),
            snapshots=snapshots,
            notes=row.get("notes") or "",
            buy_count=int(row.get("buy_count") or 0),
            sell_count=int(row.get("sell_count") or 0),
            top_buyer_share=float(row.get("top_buyer_share") or 0.0),
            volume_usd=row.get("volume_usd"),
        )

    def list_paper_signals(self, limit: int = 500) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM paper_signals ORDER BY signal_ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_paper_signal(self, mint: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM paper_signals WHERE mint = ?", (mint,)).fetchone()
        return dict(row) if row else None

    def create_execution_order(
        self,
        *,
        idempotency_key: str,
        mint: str,
        side: str,
        mode: str,
        status: str,
        signal_ts: int,
        amount_sol: float | None,
        token_amount: float | None,
        slippage_bps: int,
        error: str | None = None,
    ) -> bool:
        now = int(time.time())
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT OR IGNORE INTO execution_orders(
                    idempotency_key, mint, side, mode, status, signal_ts,
                    amount_sol, token_amount, slippage_bps, error, created_ts, updated_ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    idempotency_key,
                    mint,
                    side,
                    mode,
                    status,
                    signal_ts,
                    amount_sol,
                    token_amount,
                    slippage_bps,
                    error,
                    now,
                    now,
                ),
            )
            return cur.rowcount > 0

    def update_execution_order(
        self,
        idempotency_key: str,
        *,
        status: str | None | object = _UNSET,
        token_amount: float | None | object = _UNSET,
        tx_signature: str | None | object = _UNSET,
        realized_pnl_sol: float | None | object = _UNSET,
        response_json: str | None | object = _UNSET,
        error: str | None | object = _UNSET,
    ) -> None:
        values: list[object] = []
        setters: list[str] = []
        for name, value in (
            ("status", status),
            ("token_amount", token_amount),
            ("tx_signature", tx_signature),
            ("realized_pnl_sol", realized_pnl_sol),
            ("response_json", response_json),
            ("error", error),
        ):
            if value is not _UNSET:
                setters.append(f"{name} = ?")
                values.append(value)
        if not setters:
            return
        setters.append("updated_ts = ?")
        values.extend((int(time.time()), idempotency_key))
        with self._conn() as c:
            c.execute(
                f"UPDATE execution_orders SET {', '.join(setters)} WHERE idempotency_key = ?",
                values,
            )

    def get_execution_order(self, idempotency_key: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM execution_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row else None

    def list_execution_orders(
        self, *, since_ts: int = 0, mint: str | None = None
    ) -> list[dict]:
        query = "SELECT * FROM execution_orders WHERE created_ts >= ?"
        args: list[object] = [since_ts]
        if mint:
            query += " AND mint = ?"
            args.append(mint)
        query += " ORDER BY created_ts ASC"
        with self._conn() as c:
            rows = c.execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def upsert_position(
        self,
        *,
        mint: str,
        status: str,
        token_amount: float,
        entry_sol: float,
        entry_price_usd: float | None,
        opened_ts: int,
        peak_return_pct: float = 0.0,
        tp1_done: bool = False,
        tp2_done: bool = False,
        last_quote_ts: int | None = None,
    ) -> None:
        now = int(time.time())
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO positions(
                    mint, status, token_amount, entry_sol, entry_price_usd,
                    opened_ts, updated_ts, peak_return_pct, tp1_done, tp2_done, last_quote_ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    status=excluded.status,
                    token_amount=excluded.token_amount,
                    entry_sol=excluded.entry_sol,
                    entry_price_usd=excluded.entry_price_usd,
                    updated_ts=excluded.updated_ts,
                    peak_return_pct=excluded.peak_return_pct,
                    tp1_done=excluded.tp1_done,
                    tp2_done=excluded.tp2_done,
                    last_quote_ts=excluded.last_quote_ts
                """,
                (
                    mint,
                    status,
                    token_amount,
                    entry_sol,
                    entry_price_usd,
                    opened_ts,
                    now,
                    peak_return_pct,
                    1 if tp1_done else 0,
                    1 if tp2_done else 0,
                    last_quote_ts,
                ),
            )

    def update_position(
        self,
        mint: str,
        *,
        status: str | None | object = _UNSET,
        token_amount: float | None | object = _UNSET,
        entry_sol: float | None | object = _UNSET,
        peak_return_pct: float | None | object = _UNSET,
        tp1_done: bool | None | object = _UNSET,
        tp2_done: bool | None | object = _UNSET,
        last_quote_ts: int | None | object = _UNSET,
    ) -> None:
        values: list[object] = []
        setters: list[str] = []
        for name, value in (
            ("status", status),
            ("token_amount", token_amount),
            ("entry_sol", entry_sol),
            ("peak_return_pct", peak_return_pct),
            ("tp1_done", tp1_done),
            ("tp2_done", tp2_done),
            ("last_quote_ts", last_quote_ts),
        ):
            if value is not _UNSET:
                setters.append(f"{name} = ?")
                values.append(value)
        if not setters:
            return
        setters.append("updated_ts = ?")
        values.extend((int(time.time()), mint))
        with self._conn() as c:
            c.execute(
                f"UPDATE positions SET {', '.join(setters)} WHERE mint = ?",
                values,
            )

    def get_position(self, mint: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM positions WHERE mint = ?", (mint,)).fetchone()
        return dict(row) if row else None

    def list_open_positions(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_ts ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def today_realized_loss_sol(self) -> float:
        start = int(time.time()) // 86400 * 86400
        with self._conn() as c:
            row = c.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN realized_pnl_sol < 0 THEN -realized_pnl_sol ELSE 0 END), 0)
                FROM execution_orders
                WHERE side='exit' AND created_ts >= ?
                """,
                (start,),
            ).fetchone()
        return float(row[0] or 0.0)
