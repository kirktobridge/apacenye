"""Ledger + capture backup routine (B-5) — periodic, out-of-tree snapshots.

Ops, not money-path. This copies the SQLite ledger (the single source of truth)
and the capture tree (our only executability-honest backtest data) to a
destination OUTSIDE the working tree, so a `data/` wipe, a bad `git clean`, or a
disk move never silently loses either. B-15 promotes these backups to required
and off-box; the destination is configurable (BACKUP_DIR) for exactly that.

The ledger is copied with SQLite's ONLINE backup API, not a file copy: it
produces a transactionally consistent single-file snapshot while `serve` keeps
writing in WAL mode. A plain `cp` would miss the -wal/-shm sidecars and could
capture a torn database; the online backup folds committed WAL frames into one
clean file. Capture files are append-only gzip, so a tree copy is safe — at
worst the newest snapshot lags the final line of today's still-open file (the
capture format is already crash-tolerant about truncated trailing members).

Each run writes ONE self-contained snapshot dir
`<root>/apacenye-backup-<UTC-timestamp>/` holding `apacenye.sqlite` + `capture/`,
so any single directory restores wholesale — no chain to replay. Retention keeps
the newest N and deletes the rest. Full (not incremental) snapshots are a
deliberate simplicity choice for a size-S ops task: capture is already gzipped
and retention bounds the duplication; hardlink incrementals can come with B-15
if SD-card space ever bites.

Failures never propagate to the caller: a backup problem must not take down
trading (the same rule capture writes follow).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

BACKUP_PREFIX = "apacenye-backup-"
_TIMESTAMP_FMT = "%Y%m%dT%H%M%SZ"  # lexicographic order == chronological order


def _snapshot_name(ts: datetime) -> str:
    return BACKUP_PREFIX + ts.astimezone(timezone.utc).strftime(_TIMESTAMP_FMT)


def backup_sqlite(db_path: str | Path, dest_path: str | Path) -> None:
    """Consistent single-file copy of the ledger via SQLite's online backup
    API. Opens the source READ-ONLY and streams a live snapshot into dest_path,
    safe while serve holds the database open in WAL mode."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            src.backup(dst)
            # The copy inherits the source's WAL journal mode; fold it back to a
            # rollback journal so the snapshot is ONE standalone file (no -wal/-shm
            # sidecar to lose) — the whole point of a wholesale-restorable dir.
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    finally:
        src.close()


def create_backup(db_path: str | Path, capture_dir: str | Path,
                  backup_root: str | Path, *, now: datetime | None = None) -> Path:
    """Write one complete snapshot dir and return its path. Copies whichever of
    (ledger, capture/) exists; a fresh install with neither still yields an
    (empty) snapshot dir rather than raising."""
    now = now or datetime.now(timezone.utc)
    db_path = Path(db_path)
    capture_dir = Path(capture_dir)
    dest = Path(backup_root) / _snapshot_name(now)
    dest.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        backup_sqlite(db_path, dest / db_path.name)
    if capture_dir.exists():
        shutil.copytree(capture_dir, dest / "capture", dirs_exist_ok=True)
    return dest


def prune_backups(backup_root: str | Path, retention: int) -> list[Path]:
    """Delete all but the newest `retention` snapshot dirs; return what was
    removed. retention <= 0 keeps everything (pruning disabled). Non-snapshot
    entries under the root are never touched."""
    root = Path(backup_root)
    if retention <= 0 or not root.exists():
        return []
    snaps = sorted(
        (p for p in root.iterdir()
         if p.is_dir() and p.name.startswith(BACKUP_PREFIX)),
        key=lambda p: p.name,
    )
    if len(snaps) <= retention:
        return []
    removed: list[Path] = []
    for old in snaps[:-retention]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old)
    return removed


async def backup_loop(db_path: str | Path, capture_dir: str | Path,
                      backup_root: str | Path, *, interval_s: float,
                      retention: int, should_continue: Callable[[], bool]) -> None:
    """Snapshot-then-sleep loop for serve. Backs up immediately at start (so a
    crash in the first interval still leaves a snapshot), then every interval_s.
    Never raises: a failed run logs and the loop keeps going. `should_continue`
    bounds its lifetime (serve ties it to orchestrator liveness)."""
    while should_continue():
        try:
            dest = create_backup(db_path, capture_dir, backup_root)
            removed = prune_backups(backup_root, retention)
            log.info("backup written: %s (pruned %d beyond retention=%d)",
                     dest, len(removed), retention)
        except Exception:
            log.exception("backup run failed; continuing")
        await asyncio.sleep(interval_s)
