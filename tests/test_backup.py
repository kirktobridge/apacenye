"""Ledger + capture backup routine (B-5). Ops, not money-path, but the ledger
is the single source of truth so the snapshot must be CONSISTENT (SQLite online
backup, not a torn file copy) and the loop must never take down trading."""

import gzip
import sqlite3
from datetime import datetime, timezone

from apacenye import backup as bk


def _make_ledger(path, rows=3):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")  # serve runs WAL — back it up live
    con.execute("CREATE TABLE realizations (amount_dollars REAL)")
    con.executemany("INSERT INTO realizations VALUES (?)", [(1.0,)] * rows)
    con.commit()
    return con  # left OPEN on purpose: prove backup works while serve holds it


def _make_capture(capture_dir):
    day = capture_dir / "2026-07-20"
    day.mkdir(parents=True)
    with gzip.open(day / "book.jsonl.gz", "wt") as f:
        f.write('{"ts": "2026-07-20T00:00:00+00:00", "type": "book"}\n')


def test_create_backup_snapshots_db_and_capture(tmp_path):
    db = tmp_path / "apacenye.sqlite"
    cap = tmp_path / "capture"
    root = tmp_path / "backups"
    con = _make_ledger(db)  # kept open (WAL) to mimic a running serve
    _make_capture(cap)

    dest = bk.create_backup(db, cap, root)
    con.close()

    assert dest.parent == root
    assert dest.name.startswith(bk.BACKUP_PREFIX)
    # ledger snapshot is a real, consistent single file with the committed rows
    snap_db = dest / "apacenye.sqlite"
    assert snap_db.exists()
    got = sqlite3.connect(snap_db).execute(
        "SELECT COUNT(*) FROM realizations").fetchone()[0]
    assert got == 3
    # WAL sidecars are not needed by the snapshot: online backup folds them in
    assert not (dest / "apacenye.sqlite-wal").exists()
    # capture tree copied verbatim
    assert (dest / "capture" / "2026-07-20" / "book.jsonl.gz").exists()


def test_create_backup_with_no_db_or_capture_is_fine(tmp_path):
    # Fresh install: neither file exists yet. Snapshot dir still appears, no raise.
    dest = bk.create_backup(tmp_path / "nope.sqlite", tmp_path / "nocap",
                            tmp_path / "backups")
    assert dest.exists()
    assert not (dest / "nope.sqlite").exists()
    assert not (dest / "capture").exists()


def test_prune_keeps_newest_n(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    # Timestamp names sort lexicographically == chronologically.
    names = [bk._snapshot_name(datetime(2026, 7, 20, h, tzinfo=timezone.utc))
             for h in range(5)]
    for n in names:
        (root / n).mkdir()
    (root / "unrelated-dir").mkdir()  # non-snapshot dirs are never touched

    removed = bk.prune_backups(root, retention=2)

    kept = {p.name for p in root.iterdir()}
    assert kept == {names[3], names[4], "unrelated-dir"}
    assert {p.name for p in removed} == {names[0], names[1], names[2]}


def test_prune_retention_zero_keeps_everything(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    (root / bk._snapshot_name(datetime(2026, 7, 20, tzinfo=timezone.utc))).mkdir()
    assert bk.prune_backups(root, retention=0) == []
    assert len(list(root.iterdir())) == 1


async def test_backup_loop_survives_failure_and_continues(tmp_path, monkeypatch):
    # A failing backup must NOT kill the loop (same rule as capture writes).
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(bk, "create_backup", boom)
    # run exactly two iterations, then stop; interval 0 keeps the test instant
    should_continue = lambda: calls["n"] < 2  # noqa: E731

    await bk.backup_loop(tmp_path / "db", tmp_path / "cap", tmp_path / "root",
                         interval_s=0, retention=3, should_continue=should_continue)

    assert calls["n"] == 2  # it kept going after the first failure


async def test_backup_loop_writes_then_prunes(tmp_path):
    db = tmp_path / "apacenye.sqlite"
    _make_ledger(db).close()
    cap = tmp_path / "capture"
    _make_capture(cap)
    root = tmp_path / "backups"
    n = {"i": 0}
    should_continue = lambda: n["i"] < 3 and (n.__setitem__("i", n["i"] + 1) or True)  # noqa: E731

    await bk.backup_loop(db, cap, root, interval_s=0, retention=2,
                         should_continue=should_continue)

    # 3 iterations, retention 2 → at most 2 snapshots survive (names may collide
    # within the same second, so assert the cap, not an exact count)
    snaps = [p for p in root.iterdir() if p.name.startswith(bk.BACKUP_PREFIX)]
    assert 1 <= len(snaps) <= 2
