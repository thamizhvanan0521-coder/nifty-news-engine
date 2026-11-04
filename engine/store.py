"""SQLite store. Status flow:
new -> stale (too old) | skipped (low prescore) | dup (near-duplicate title)
    -> pending -> classified
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import NewsItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  uid TEXT PRIMARY KEY,
  source TEXT, tier TEXT, title TEXT, summary TEXT, url TEXT,
  symbols TEXT, category_hint TEXT,
  published_utc TEXT, fetched_utc TEXT,
  prescore REAL DEFAULT 0,
  status TEXT DEFAULT 'new',
  dup_of TEXT,
  impact INTEGER, direction TEXT, classification TEXT,
  alerted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_items_status ON items(status);
CREATE INDEX IF NOT EXISTS ix_items_pub ON items(published_utc);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # Plain rollback journal (not WAL): the cloud job saves this single file between runs.
        self.conn.executescript(SCHEMA)

    # ---------- ingest ----------
    def insert_new(self, items: list[NewsItem]) -> list[NewsItem]:
        fresh, now = [], _iso(datetime.now(timezone.utc))
        with self.conn:
            for it in items:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO items (uid, source, tier, title, summary, url, symbols,"
                    " category_hint, published_utc, fetched_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (it.uid, it.source, it.tier, it.title, it.summary, it.url,
                     json.dumps(it.symbols), it.category_hint, _iso(it.published), now))
                if cur.rowcount:
                    fresh.append(it)
        return fresh

    def set_status(self, uid: str, status: str, prescore: float | None = None,
                   dup_of: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE items SET status=?, prescore=COALESCE(?, prescore), dup_of=? WHERE uid=?",
                (status, prescore, dup_of, uid))

    def recent_titles(self, hours: int) -> list[tuple[str, str]]:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
        rows = self.conn.execute(
            "SELECT uid, title FROM items WHERE status IN ('pending','classified')"
            " AND tier != 'corporate' AND published_utc >= ?", (cutoff,)).fetchall()
        return [(r["uid"], r["title"]) for r in rows]

    # ---------- classification ----------
    def pending(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM items WHERE status='pending'"
            " ORDER BY prescore DESC, published_utc DESC LIMIT ?", (limit,)).fetchall()

    def expire_pending(self, max_age_hours: int) -> int:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(hours=max_age_hours))
        with self.conn:
            cur = self.conn.execute(
                "UPDATE items SET status='stale' WHERE status='pending' AND published_utc < ?",
                (cutoff,))
        return cur.rowcount

    def save_classification(self, uid: str, cls: dict) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE items SET status='classified', impact=?, direction=?, classification=?"
                " WHERE uid=?",
                (int(cls.get("impact", 0)), cls.get("direction", ""), json.dumps(cls), uid))

    def mark_alerted(self, uid: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE items SET alerted=1 WHERE uid=?", (uid,))

    # ---------- reads ----------
    def classified_since(self, since: datetime, min_impact: int, limit: int = 80) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM items WHERE status='classified' AND impact >= ? AND published_utc >= ?"
            " ORDER BY impact DESC, published_utc DESC LIMIT ?",
            (min_impact, _iso(since), limit)).fetchall()

    def prune(self, days: int) -> int:
        """Delete rows older than `days` so the state file stays small in the cloud cache."""
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self.conn:
            cur = self.conn.execute("DELETE FROM items WHERE fetched_utc < ?", (cutoff,))
        self.conn.execute("VACUUM")
        return cur.rowcount

    def counts_by_status(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT status, COUNT(*) FROM items GROUP BY status")}

    # ---------- meta / counters ----------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?)"
                              " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def incr_counter(self, key: str, by: int = 1) -> int:
        n = int(self.get_meta(key, "0")) + by
        self.set_meta(key, str(n))
        return n
