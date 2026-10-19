"""
SQLite persistence: seen news articles (with their sentiment scores),
Telegram subscribers, and small key/value state (last signal etc.).

Only the standard library is used, so there is nothing extra to install.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    link         TEXT,
    source       TEXT,
    summary      TEXT,
    published_at TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    impact       REAL,
    confidence   REAL,
    theme        TEXT,
    reason       TEXT,
    scored_by    TEXT,
    high_impact  INTEGER DEFAULT 0,
    alerted      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id    INTEGER PRIMARY KEY,
    alerts     INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    side        TEXT NOT NULL,
    entry       REAL NOT NULL,
    sl          REAL NOT NULL,
    sl_current  REAL,
    tp1         REAL NOT NULL,
    tp2         REAL NOT NULL,
    confidence  INTEGER,
    opened_at   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    closed_at   TEXT,
    exit_price  REAL,
    result_r    REAL,
    tp1_hit_at  TEXT,
    reason      TEXT,
    zone_lo     REAL,
    zone_hi     REAL,
    chase       REAL,
    triggered_at TEXT,
    trigger_note TEXT,
    setup_mode  TEXT,
    touched     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signal_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    price     REAL,
    at        TEXT NOT NULL,
    text      TEXT
);
"""


@dataclass
class Article:
    id: str
    title: str
    link: str
    source: str
    summary: str
    published_at: datetime
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    impact: float | None = None       # -2 .. +2  (negative = bearish for gold)
    confidence: float | None = None   # 0 .. 1
    theme: str | None = None
    reason: str | None = None
    scored_by: str | None = None      # "lexicon" | "claude"
    high_impact: bool = False
    alerted: bool = False

    @property
    def age_hours(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.published_at).total_seconds() / 3600.0)

    @property
    def direction(self) -> str:
        if self.impact is None or abs(self.impact) < 0.35:
            return "neutral"
        return "bullish" if self.impact > 0 else "bearish"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()   # re-entrant: locked methods may call each other
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate()

    # ---- articles ----------------------------------------------------------
    def has_article(self, article_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone()
            return row is not None

    def upsert_articles(self, articles: Iterable[Article]) -> int:
        """Insert new articles (skip ids already stored). Returns inserted count."""
        inserted = 0
        with self._lock, self._conn:
            for a in articles:
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO articles
                       (id, title, link, source, summary, published_at, fetched_at,
                        impact, confidence, theme, reason, scored_by, high_impact, alerted)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        a.id, a.title, a.link, a.source, a.summary,
                        _iso(a.published_at), _iso(a.fetched_at),
                        a.impact, a.confidence, a.theme, a.reason, a.scored_by,
                        int(a.high_impact), int(a.alerted),
                    ),
                )
                inserted += cur.rowcount
        return inserted

    def update_scores(self, articles: Iterable[Article]) -> None:
        with self._lock, self._conn:
            for a in articles:
                self._conn.execute(
                    """UPDATE articles SET impact=?, confidence=?, theme=?, reason=?, scored_by=?,
                       high_impact=? WHERE id=?""",
                    (a.impact, a.confidence, a.theme, a.reason, a.scored_by, int(a.high_impact), a.id),
                )

    def mark_alerted(self, ids: Iterable[str]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("UPDATE articles SET alerted=1 WHERE id=?", [(i,) for i in ids])

    def recent_articles(self, hours: float, limit: int = 200) -> list[Article]:
        with self._lock:
            since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
            rows = self._conn.execute(
                "SELECT * FROM articles WHERE published_at >= ? ORDER BY published_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
            return [self._row_to_article(r) for r in rows]

    def unscored_articles(self, limit: int = 50) -> list[Article]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM articles WHERE impact IS NULL ORDER BY published_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_article(r) for r in rows]

    def unalerted_high_impact(self, hours: float = 6) -> list[Article]:
        with self._lock:
            since = _iso(datetime.now(timezone.utc) - timedelta(hours=hours))
            rows = self._conn.execute(
                """SELECT * FROM articles WHERE high_impact=1 AND alerted=0 AND published_at >= ?
                   ORDER BY published_at DESC""",
                (since,),
            ).fetchall()
            return [self._row_to_article(r) for r in rows]

    def prune(self, days: int = 14) -> int:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM articles WHERE published_at < ?", (cutoff,))
            return cur.rowcount

    @staticmethod
    def _row_to_article(r: sqlite3.Row) -> Article:
        return Article(
            id=r["id"], title=r["title"], link=r["link"] or "", source=r["source"] or "",
            summary=r["summary"] or "", published_at=_parse(r["published_at"]),
            fetched_at=_parse(r["fetched_at"]), impact=r["impact"], confidence=r["confidence"],
            theme=r["theme"], reason=r["reason"], scored_by=r["scored_by"],
            high_impact=bool(r["high_impact"]), alerted=bool(r["alerted"]),
        )

    # ---- subscribers -------------------------------------------------------
    def subscribe(self, chat_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO subscribers (chat_id, alerts, created_at) VALUES (?, 1, ?)",
                (chat_id, _iso(datetime.now(timezone.utc))),
            )

    def unsubscribe(self, chat_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))

    def set_alerts(self, chat_id: int, enabled: bool) -> None:
        self.subscribe(chat_id)
        with self._lock, self._conn:
            self._conn.execute("UPDATE subscribers SET alerts=? WHERE chat_id=?", (int(enabled), chat_id))

    def subscribers(self, alerts_only: bool = False) -> list[int]:
        with self._lock:
            q = "SELECT chat_id FROM subscribers" + (" WHERE alerts=1" if alerts_only else "")
            return [int(r["chat_id"]) for r in self._conn.execute(q).fetchall()]

    def is_subscribed(self, chat_id: int) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone() is not None

    def alerts_enabled(self, chat_id: int) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT alerts FROM subscribers WHERE chat_id=?", (chat_id,)).fetchone()
            return bool(row and row["alerts"])

    # ---- state -------------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            if row is None or row["value"] is None:
                return default
            try:
                return json.loads(row["value"])
            except json.JSONDecodeError:
                return row["value"]

    def set_state(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, default=str)),
            )

    def _migrate(self) -> None:
        """Add columns introduced after the first release (SQLite has no IF NOT EXISTS for columns)."""
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(signals)").fetchall()}
        for col, typ in (("zone_lo", "REAL"), ("zone_hi", "REAL"), ("chase", "REAL"), ("triggered_at", "TEXT"),
                         ("trigger_note", "TEXT"), ("setup_mode", "TEXT"), ("touched", "INTEGER DEFAULT 0")):
            if col not in have:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {typ}")

    # ---- trade signals -----------------------------------------------------
    def insert_signal(self, sig) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO signals (side, entry, sl, sl_current, tp1, tp2, confidence, opened_at, status, reason,
                                        zone_lo, zone_hi, chase, triggered_at, trigger_note, setup_mode, touched)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sig.side, sig.entry, sig.sl, sig.sl_current, sig.tp1, sig.tp2, sig.confidence, _iso(sig.opened_at), sig.status, sig.reason,
                 sig.zone_lo, sig.zone_hi, sig.chase, _iso(sig.triggered_at) if sig.triggered_at else None, sig.trigger_note, sig.setup_mode, int(sig.touched)),
            )
            return int(cur.lastrowid)

    def update_signal(self, sig) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE signals SET status=?, closed_at=?, exit_price=?, result_r=?, tp1_hit_at=?, sl_current=?,
                                      entry=?, sl=?, tp1=?, tp2=?, zone_lo=?, zone_hi=?, chase=?, triggered_at=?, trigger_note=?, setup_mode=?, confidence=?, touched=?
                   WHERE id=?""",
                (sig.status, _iso(sig.closed_at) if sig.closed_at else None, sig.exit_price, sig.result_r,
                 _iso(sig.tp1_hit_at) if sig.tp1_hit_at else None, sig.sl_current,
                 sig.entry, sig.sl, sig.tp1, sig.tp2, sig.zone_lo, sig.zone_hi, sig.chase,
                 _iso(sig.triggered_at) if sig.triggered_at else None, sig.trigger_note, sig.setup_mode, sig.confidence, int(sig.touched), sig.id),
            )

    def _row_to_signal(self, r: sqlite3.Row):
        from signals.tracker import TradeSignal  # local import to avoid a cycle

        return TradeSignal(
            id=int(r["id"]), side=r["side"], entry=float(r["entry"]), sl=float(r["sl"]), tp1=float(r["tp1"]), tp2=float(r["tp2"]),
            confidence=int(r["confidence"] or 0), opened_at=_parse(r["opened_at"]), status=r["status"],
            closed_at=_parse(r["closed_at"]) if r["closed_at"] else None, exit_price=r["exit_price"], result_r=r["result_r"],
            tp1_hit_at=_parse(r["tp1_hit_at"]) if r["tp1_hit_at"] else None, reason=r["reason"] or "", sl_current=r["sl_current"],
            zone_lo=r["zone_lo"], zone_hi=r["zone_hi"], chase=r["chase"],
            triggered_at=_parse(r["triggered_at"]) if r["triggered_at"] else None,
            trigger_note=r["trigger_note"] or "", setup_mode=r["setup_mode"] or "",
            touched=bool(r["touched"]) if "touched" in r.keys() else False,
        )

    def active_signal(self):
        with self._lock:
            row = self._conn.execute("SELECT * FROM signals WHERE status IN ('active','tp1') ORDER BY id DESC LIMIT 1").fetchone()
            return self._row_to_signal(row) if row else None

    def pending_signal(self):
        with self._lock:
            row = self._conn.execute("SELECT * FROM signals WHERE status = 'pending' ORDER BY id DESC LIMIT 1").fetchone()
            return self._row_to_signal(row) if row else None

    def signals(self, limit: int = 20) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._row_to_signal(r) for r in rows]

    def add_signal_event(self, signal_id: int, kind: str, price: float | None, text: str, at: datetime | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO signal_events (signal_id, kind, price, at, text) VALUES (?,?,?,?,?)",
                (signal_id, kind, price, _iso(at or datetime.now(timezone.utc)), text),
            )

    def signal_events(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.*, s.side FROM signal_events e JOIN signals s ON s.id = e.signal_id ORDER BY e.id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{"id": r["id"], "signal_id": r["signal_id"], "kind": r["kind"], "price": r["price"], "at": r["at"], "text": r["text"], "side": r["side"]} for r in rows]

    def serialize(self) -> bytes:
        """The whole database as a SQLite file image (for backups); consistent under the store lock."""
        with self._lock:
            if hasattr(self._conn, "serialize"):          # Python ≥ 3.11
                return self._conn.serialize()
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:    # older Pythons: online backup to a file
                p = Path(tmp) / "snapshot.db"
                dst = sqlite3.connect(str(p))
                try:
                    self._conn.backup(dst)
                finally:
                    dst.close()
                return p.read_bytes()

    def close(self) -> None:
        self._conn.close()


def article_to_dict(a: Article) -> dict[str, Any]:
    d = asdict(a)
    d["published_at"] = _iso(a.published_at)
    d["fetched_at"] = _iso(a.fetched_at)
    return d
