"""SQLite persistence layer for CardVault.

The `cards` table only ever holds ciphertext produced client-side.
Plaintext card numbers must never reach this module.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

DB_PATH = os.environ.get("CARDVAULT_DB", os.path.join(os.path.dirname(__file__), "cardvault.db"))

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    label TEXT NOT NULL,
    brand TEXT NOT NULL,
    last4 TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    iv TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def init_db(path: str | None = None) -> None:
    global DB_PATH
    if path:
        DB_PATH = path
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


# ---------- users ----------

def create_user(email: str, password_hash: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email.lower().strip(), password_hash, time.time()),
        )
        return int(cur.lastrowid)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def set_plan(user_id: int, plan: str, stripe_customer_id: str | None = None) -> None:
    with connect() as conn:
        if stripe_customer_id is not None:
            conn.execute(
                "UPDATE users SET plan = ?, stripe_customer_id = ? WHERE id = ?",
                (plan, stripe_customer_id, user_id),
            )
        else:
            conn.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))


def get_user_by_stripe_customer(customer_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE stripe_customer_id = ?", (customer_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------- sessions ----------

SESSION_TTL = 60 * 60 * 24 * 7  # one week


def create_session(token: str, user_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, time.time() + SESSION_TTL),
        )


def get_session_user(token: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, time.time()),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ---------- cards (encrypted blobs only) ----------

def count_cards(user_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM cards WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["n"])


def add_card(
    user_id: int, label: str, brand: str, last4: str, ciphertext: str, iv: str, salt: str
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO cards (user_id, label, brand, last4, ciphertext, iv, salt, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, label, brand, last4, ciphertext, iv, salt, time.time()),
        )
        return int(cur.lastrowid)


def list_cards(user_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_card(user_id: int, card_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM cards WHERE id = ? AND user_id = ?", (card_id, user_id)
        )
        return cur.rowcount > 0
