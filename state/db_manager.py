import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

import config


@contextmanager
def _conn():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol  TEXT PRIMARY KEY,
            added_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS boxes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            box_top         REAL NOT NULL,
            box_bottom      REAL NOT NULL,
            high_date       TEXT,
            confirmed_date  TEXT,
            status          TEXT NOT NULL DEFAULT 'forming'
        );

        CREATE TABLE IF NOT EXISTS positions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            entry_price     REAL NOT NULL,
            quantity        INTEGER NOT NULL,
            entry_date      TEXT NOT NULL,
            box_id          INTEGER,
            pyramid_level   INTEGER NOT NULL DEFAULT 1,
            status          TEXT NOT NULL DEFAULT 'open',
            exit_price      REAL,
            exit_date       TEXT
        );

        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            price       REAL NOT NULL,
            quantity    INTEGER,
            details     TEXT,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)


# ── Watchlist ──────────────────────────────────────────────────────────────

def add_to_watchlist(symbol: str):
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES (?, ?)",
            (symbol, datetime.utcnow().isoformat()),
        )


def remove_from_watchlist(symbol: str):
    with _conn() as con:
        con.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))


def get_watchlist() -> list[str]:
    with _conn() as con:
        rows = con.execute("SELECT symbol FROM watchlist ORDER BY added_at").fetchall()
    return [r["symbol"] for r in rows]


# ── Boxes ──────────────────────────────────────────────────────────────────

def upsert_box(
    symbol: str,
    box_top: float,
    box_bottom: float,
    high_date: Optional[str],
    confirmed_date: Optional[str],
    status: str,
) -> int:
    """
    Insert or update the most recent box for a symbol.
    Updates the existing 'forming'/'confirmed' row if one exists, else inserts.
    Returns the row id.
    """
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM boxes WHERE symbol=? AND status IN ('forming','confirmed') "
            "ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()

        if existing:
            con.execute(
                "UPDATE boxes SET box_top=?, box_bottom=?, high_date=?, "
                "confirmed_date=?, status=? WHERE id=?",
                (box_top, box_bottom, high_date, confirmed_date, status, existing["id"]),
            )
            return existing["id"]

        cur = con.execute(
            "INSERT INTO boxes(symbol, box_top, box_bottom, high_date, confirmed_date, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, box_top, box_bottom, high_date, confirmed_date, status),
        )
        return cur.lastrowid


def get_active_box(symbol: str) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM boxes WHERE symbol=? AND status IN ('forming','confirmed') "
            "ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    return dict(row) if row else None


def get_all_confirmed_boxes() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM boxes WHERE status='confirmed' ORDER BY symbol"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Signals ────────────────────────────────────────────────────────────────

def add_signal(
    symbol: str,
    signal_type: str,
    price: float,
    quantity: Optional[int] = None,
    details: Optional[str] = None,
):
    with _conn() as con:
        con.execute(
            "INSERT INTO signals(symbol, signal_type, price, quantity, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, signal_type, price, quantity, details, datetime.utcnow().isoformat()),
        )


def get_today_signals() -> list[dict]:
    today = date.today().isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM signals WHERE created_at LIKE ? ORDER BY created_at DESC",
            (f"{today}%",),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Positions ──────────────────────────────────────────────────────────────

def add_position(
    symbol: str,
    entry_price: float,
    quantity: int,
    entry_date: str,
    box_id: Optional[int] = None,
    pyramid_level: int = 1,
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO positions(symbol, entry_price, quantity, entry_date, box_id, "
            "pyramid_level, status) VALUES (?, ?, ?, ?, ?, ?, 'open')",
            (symbol, entry_price, quantity, entry_date, box_id, pyramid_level),
        )
        return cur.lastrowid


def get_open_positions() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY entry_date"
        ).fetchall()
    return [dict(r) for r in rows]


def close_position(position_id: int, exit_price: float, exit_date: str):
    with _conn() as con:
        con.execute(
            "UPDATE positions SET status='closed', exit_price=?, exit_date=? WHERE id=?",
            (exit_price, exit_date, position_id),
        )


# ── Settings ───────────────────────────────────────────────────────────────

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
