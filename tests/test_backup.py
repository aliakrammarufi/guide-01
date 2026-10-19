"""State backup: snapshot → restore round trip against an in-memory stand-in for Redis."""
from __future__ import annotations

import zlib
from unittest import mock

import backup
from news.store import Store


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v

    def pipeline(self):
        return self

    def execute(self):
        return []


def test_snapshot_and_restore_roundtrip(tmp_path):
    fake = FakeRedis()
    with mock.patch.object(backup, "_client", lambda url: fake):
        st = Store(tmp_path / "live.db")
        st.subscribe(42)
        st.set_state("risk", {"account": 1000})
        b = backup.StateBackup(st, "redis://fake", 60)
        assert b.snapshot(), b.last_error
        assert b.last_ok is not None and b.last_bytes > 0
        assert zlib.decompress(fake.kv[backup.KEY]).startswith(b"SQLite format 3")

        # a fresh container: no database file → restored from the store
        assert backup.restore(tmp_path / "fresh.db", "redis://fake")
        st2 = Store(tmp_path / "fresh.db")
        assert st2.subscribers() == [42]
        assert st2.get_state("risk") == {"account": 1000}

        # an existing database is never overwritten by the remote copy
        assert backup.restore(tmp_path / "fresh.db", "redis://fake") is False


def test_restore_without_snapshot_or_url(tmp_path):
    fake = FakeRedis()
    with mock.patch.object(backup, "_client", lambda url: fake):
        assert backup.restore(tmp_path / "x.db", "redis://fake") is False
    assert backup.restore(tmp_path / "x.db", "") is False
    assert not (tmp_path / "x.db").exists()


def test_snapshot_failure_is_quiet_and_retried(tmp_path):
    class Broken(FakeRedis):
        def execute(self):
            raise ConnectionError("down")

    with mock.patch.object(backup, "_client", lambda url: Broken()):
        st = Store(tmp_path / "a.db")
        b = backup.StateBackup(st, "redis://fake", 60)
        assert b.snapshot() is False
        assert "down" in (b.last_error or "")
        assert "not saved yet" in b.describe()
    assert backup.StateBackup(st, "", 60).enabled is False
