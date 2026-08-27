"""
Rate limiting for the public demo — this endpoint has no auth, so this is
the only thing standing between it and unbounded Claude/Flixbus traffic.

Two independent caps, both backed by a small local SQLite file so they
survive a process restart:
  - one attempt per IP per calendar day (UTC)
  - a global daily cap across all visitors, as a backstop against a burst
    of traffic from many IPs (or one visitor behind a rotating/shared IP)
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("DEMO_RATE_LIMIT_DB", "demo_rate_limit.db"))
GLOBAL_DAILY_CAP = int(os.environ.get("DEMO_GLOBAL_DAILY_CAP", "20"))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attempts (ip TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (ip, day))"
    )
    return conn


def check_and_record(ip: str) -> tuple[bool, str | None]:
    """
    Atomically checks both caps and, if allowed, records this attempt so a
    second concurrent request from the same IP can't slip through. Returns
    (allowed, reason) — reason is set only when allowed is False.
    """
    today = _today()
    with closing(_connect()) as conn:
        with conn:  # transaction: check + insert happen atomically
            row = conn.execute(
                "SELECT 1 FROM attempts WHERE ip = ? AND day = ?", (ip, today)
            ).fetchone()
            if row is not None:
                return False, "You've already used today's demo try — come back tomorrow."

            (count,) = conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE day = ?", (today,)
            ).fetchone()
            if count >= GLOBAL_DAILY_CAP:
                return False, "The demo has hit its daily visitor cap — come back tomorrow."

            conn.execute("INSERT INTO attempts (ip, day) VALUES (?, ?)", (ip, today))
            return True, None
