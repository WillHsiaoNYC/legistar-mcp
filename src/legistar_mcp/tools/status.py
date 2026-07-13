"""Index freshness and coverage. The archive only updates when the user re-runs
`legistar-mcp index`; without this signal an agent answers "what's coming up /
what's new" confidently from a weeks-old snapshot."""
from __future__ import annotations

import datetime as _dt
from sqlite3 import Connection

from ..db import SCHEMA_VERSION
from ._snippet import _archive_root

_STALE_AFTER_DAYS = 7


def _last_indexed(conn: Connection) -> _dt.datetime | None:
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'last_indexed'"
    ).fetchone()
    if row is None:
        return None
    try:
        return _dt.datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def _index_age_days(conn: Connection) -> int | None:
    stamp = _last_indexed(conn)
    if stamp is None:
        return None
    return max(0, (_dt.datetime.now(_dt.timezone.utc) - stamp).days)


def staleness_warning(conn: Connection, max_age_days: int = _STALE_AFTER_DAYS) -> str | None:
    """One-line warning when the index is old (or of unknown age). Embedded in
    time-window tools because those are where staleness silently lies."""
    age = _index_age_days(conn)
    if age is None:
        return (
            "Index age unknown (no last_indexed recorded — indexed by an older "
            "release). Refresh with `git pull` in the archive then `legistar-mcp index`."
        )
    if age > max_age_days:
        return (
            f"Index last updated {age} days ago — recent bills/hearings may be "
            f"missing. Refresh with `git pull` in the archive then `legistar-mcp index`."
        )
    return None


def data_status(conn: Connection) -> dict:
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("bills", "events", "people", "votes", "event_items")
    }
    cov = conn.execute(
        "SELECT MIN(intro_date), MAX(intro_date) FROM bills"
    ).fetchone()
    ecov = conn.execute("SELECT MIN(date), MAX(date) FROM events").fetchone()
    stamp = _last_indexed(conn)
    warnings = [w for w in (staleness_warning(conn),) if w]
    db_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if db_version < SCHEMA_VERSION:
        warnings.append(
            f"DB schema version {db_version} < code version {SCHEMA_VERSION}: "
            f"run `legistar-mcp index --full` to backfill."
        )
    root = _archive_root(conn)
    return {
        "archive_root": str(root) if root else None,
        "last_indexed": stamp.isoformat() if stamp else None,
        "index_age_days": _index_age_days(conn),
        "schema_version_db": db_version,
        "schema_version_code": SCHEMA_VERSION,
        "counts": counts,
        "coverage": {
            "first_intro_date": cov[0],
            "last_intro_date": cov[1],
            "first_event_date": ecov[0],
            "last_event_date": ecov[1],
        },
        "warnings": warnings,
    }
