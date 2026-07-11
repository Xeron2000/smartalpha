from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from smartalpha.types import Side, TradeEvent


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
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    side TEXT NOT NULL,
                    sol_delta REAL NOT NULL,
                    token_delta REAL NOT NULL,
                    signature TEXT NOT NULL UNIQUE,
                    ts INTEGER NOT NULL,
                    tier TEXT,
                    weight REAL
                );
                CREATE INDEX IF NOT EXISTS idx_events_mint_ts ON events(mint, ts);
                CREATE INDEX IF NOT EXISTS idx_events_wallet_ts ON events(wallet, ts);
                CREATE TABLE IF NOT EXISTS poll_state (
                    wallet TEXT PRIMARY KEY,
                    last_signature TEXT
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    mint TEXT,
                    payload TEXT NOT NULL,
                    ts INTEGER NOT NULL
                );
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
                    hot_organic_buyers INTEGER,
                    hot_funders_json TEXT,
                    liquidity_usd REAL,
                    strict_signal INTEGER NOT NULL DEFAULT 0,
                    price_usd REAL,
                    snapshots_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_paper_signal_ts ON paper_signals(signal_ts);
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
                ("snapshots_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("notes", "TEXT"),
                ("hot_funders_json", "TEXT"),
                ("hot_organic_buyers", "INTEGER"),
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

    def is_mint_seen(self, mint: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM seen_mints WHERE mint = ? AND status='done'", (mint,)
            ).fetchone()
        return row is not None

    def mark_mint_seen(self, mint: str, signature: str, creator: str) -> None:
        # ponytail: backward-compat shim, new code uses try_seen_mint
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO seen_mints(mint,signature,creator,status,ts) "
                "VALUES (?,?,?,'done',?)",
                (mint, signature, creator, int(time.time())),
            )

    def save_event(self, ev: TradeEvent) -> bool:
        with self._conn() as c:
            try:
                c.execute(
                    """
                    INSERT INTO events(wallet,mint,side,sol_delta,token_delta,signature,ts,tier,weight)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ev.wallet,
                        ev.mint,
                        ev.side.value,
                        ev.sol_delta,
                        ev.token_delta,
                        ev.signature,
                        ev.ts,
                        ev.tier,
                        ev.weight,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def events_since(self, since_ts: int, mint: str | None = None) -> list[TradeEvent]:
        q = "SELECT * FROM events WHERE ts >= ?"
        args: list[object] = [since_ts]
        if mint:
            q += " AND mint = ?"
            args.append(mint)
        q += " ORDER BY ts ASC"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [_row_to_event(r) for r in rows]

    def get_last_sig(self, wallet: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT last_signature FROM poll_state WHERE wallet = ?", (wallet,)
            ).fetchone()
        return row["last_signature"] if row else None

    def set_last_sig(self, wallet: str, sig: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO poll_state(wallet, last_signature) VALUES (?,?)
                ON CONFLICT(wallet) DO UPDATE SET last_signature=excluded.last_signature
                """,
                (wallet, sig),
            )

    def save_alert(self, kind: str, mint: str | None, payload: str, ts: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO alerts(kind,mint,payload,ts) VALUES (?,?,?,?)",
                (kind, mint, payload, ts),
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
        hot_organic_buyers: int,
        hot_funders: list[str],
        liquidity_usd: float | None,
        strict_signal: bool,
        price_usd: float | None,
        snapshots: dict[str, dict],
        notes: str = "",
    ) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO paper_signals(
                    mint, signal_ts, creator, signature, recommendation, copytrap_risk,
                    hot_organic_buyers, hot_funders_json, liquidity_usd, strict_signal,
                    price_usd, snapshots_json, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    signal_ts=excluded.signal_ts,
                    recommendation=excluded.recommendation,
                    copytrap_risk=excluded.copytrap_risk,
                    hot_organic_buyers=excluded.hot_organic_buyers,
                    hot_funders_json=excluded.hot_funders_json,
                    liquidity_usd=excluded.liquidity_usd,
                    strict_signal=excluded.strict_signal,
                    price_usd=excluded.price_usd,
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
                    hot_organic_buyers,
                    json.dumps(hot_funders),
                    liquidity_usd,
                    1 if strict_signal else 0,
                    price_usd,
                    json.dumps(snapshots),
                    notes,
                ),
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


def _row_to_event(r: sqlite3.Row) -> TradeEvent:

    return TradeEvent(
        wallet=r["wallet"],
        mint=r["mint"],
        side=Side(r["side"]),
        sol_delta=r["sol_delta"],
        token_delta=r["token_delta"],
        signature=r["signature"],
        ts=r["ts"],
        tier=r["tier"] or "accumulator",
        weight=float(r["weight"] or 1.0),
    )
