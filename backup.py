"""
State backup for hosts without a persistent disk.

On a free-tier host (Render) the filesystem is wiped on every deploy or restart,
which would throw away the open call, the signal history and statistics, the
subscriber list and any /risk settings.  When ``REDIS_URL`` points at a
Redis-compatible store (Render Key Value, Upstash…), the whole SQLite database
is snapshotted there every couple of minutes and once more on shutdown, and a
fresh instance restores it before it opens the database.

Everything here is best effort: a missing or unreachable store is logged once
and never stops the bot.  Without ``REDIS_URL`` the module is inert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

KEY = "xau-sentinel:db"           # zlib-compressed SQLite image
META_KEY = "xau-sentinel:db:meta"  # {"saved_at", "bytes", "raw_bytes"}
MAX_RAW_BYTES = 40 * 1024 * 1024   # refuse to snapshot a database that has grown past this (free stores are small)


def _client(url: str):
    import redis  # lazy: only needed when REDIS_URL is set

    return redis.Redis.from_url(url, socket_timeout=8, socket_connect_timeout=5, health_check_interval=30)


def restore(path: Path | str, url: str) -> bool:
    """Write the last snapshot to ``path`` if there is no database there yet.

    Called before the ``Store`` opens.  An existing (non-empty) file is never
    overwritten – a developer's local database wins over the remote copy.
    """
    if not url:
        return False
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        log.info("State backup: local database present – not restoring")
        return False
    try:
        r = _client(url)
        blob = r.get(KEY)
        meta = r.get(META_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("State backup: restore skipped – store unreachable (%s)", exc)
        return False
    if not blob:
        log.info("State backup: no snapshot in the store yet – starting fresh")
        return False
    try:
        raw = zlib.decompress(blob)
        if not raw.startswith(b"SQLite format 3\x00"):
            raise ValueError("snapshot is not a SQLite image")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".restore")
        tmp.write_bytes(raw)
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("State backup: restore failed (%s)", exc)
        return False
    saved_at = ""
    try:
        saved_at = json.loads(meta).get("saved_at", "") if meta else ""
    except Exception:  # noqa: BLE001
        pass
    log.info("State backup: restored %d KB from the store (saved %s)", len(raw) // 1024, saved_at or "unknown")
    return True


class StateBackup:
    """Periodic snapshots of the Store's SQLite database into the key/value store."""

    def __init__(self, store, url: str, interval_sec: int = 120):
        self.store = store
        self.url = url
        self.interval = max(30, int(interval_sec))
        self._r = None
        self.last_ok: datetime | None = None
        self.last_error: str | None = None
        self.last_bytes = 0
        self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _redis(self):
        if self._r is None:
            self._r = _client(self.url)
        return self._r

    def snapshot(self) -> bool:
        """Serialize the database and store it. Returns True on success (safe to call from any thread)."""
        if not self.url:
            return False
        try:
            raw = self.store.serialize()
            if len(raw) > MAX_RAW_BYTES:
                raise ValueError(f"database too large to snapshot ({len(raw) // 1024} KB)")
            blob = zlib.compress(raw, 6)
            meta = json.dumps({"saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "bytes": len(blob), "raw_bytes": len(raw)})
            pipe = self._redis().pipeline()
            pipe.set(KEY, blob)
            pipe.set(META_KEY, meta)
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:200]
            self._r = None                      # reconnect next time
            if not self._warned:
                log.warning("State backup failed: %s (will keep retrying quietly)", exc)
                self._warned = True
            return False
        self.last_ok, self.last_error, self.last_bytes = datetime.now(timezone.utc), None, len(blob)
        if self._warned:
            log.info("State backup working again")
            self._warned = False
        return True

    async def run(self) -> None:
        """Snapshot every ``interval`` seconds until cancelled; one last snapshot on the way out."""
        if not self.url:
            return
        log.info("State backup: snapshots every %d s", self.interval)
        try:
            while True:
                await asyncio.sleep(self.interval)
                await asyncio.to_thread(self.snapshot)
        except asyncio.CancelledError:
            await asyncio.to_thread(self.snapshot)
            raise

    def status(self) -> dict:
        return {"enabled": self.enabled,
                "last_ok": self.last_ok.isoformat(timespec="seconds") if self.last_ok else None,
                "error": self.last_error, "bytes": self.last_bytes}

    def describe(self) -> str:
        """One short line for /status."""
        if not self.enabled:
            return "off · history is lost on redeploy"
        if self.last_ok:
            age = int(time.time() - self.last_ok.timestamp())
            return f"on · saved {age // 60} min ago" if age >= 60 else "on · saved just now"
        return f"on · not saved yet{' (' + self.last_error + ')' if self.last_error else ''}"
