"""Kill switch — out-of-band by construction (Stage 3 §5).

Plain-language summary: the kill state IS the existence of the sentinel file
`data/KILL`. The CLI can create it with the server down, hung, or crashed —
it needs only filesystem access. The risk engine `os.stat`s the file
immediately before every execution submission, and the orchestrator runs a
watcher that polls it every 2 seconds to pause workers and cancel resting
orders.

Halt semantics: reject new opens, cancel resting, pause workers, and LEAVE
OPEN POSITIONS IN PLACE — positions are fully collateralized, so a halt
cannot make them lose more than already paid, while auto-liquidating would
cross spreads and pay fees, converting a precaution into a guaranteed cost.

Un-kill is CLI-only (`apacenye unkill`, typed confirmation). There is no
HTTP un-kill endpoint anywhere in this codebase, on purpose.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class KillSwitch:
    def __init__(self, sentinel_path: str | Path):
        self.path = Path(sentinel_path)

    def is_killed(self) -> bool:
        """Cheap existence check — called before EVERY execution submission."""
        try:
            os.stat(self.path)
            return True
        except FileNotFoundError:
            return False

    def trip(self, source: str, reason: str) -> None:
        """Write the sentinel. Idempotent: an existing kill is left intact
        (the first reason is the one that matters for the post-mortem)."""
        if self.is_killed():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "reason": reason,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.path)  # atomic on POSIX

    def read_state(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            # A corrupt/unreadable sentinel still means KILLED — existence is
            # the authority, the JSON is only metadata.
            return {"ts": None, "source": "unknown", "reason": "unreadable sentinel"}

    def clear(self) -> None:
        """Remove the sentinel. ONLY the CLI unkill path may call this."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
