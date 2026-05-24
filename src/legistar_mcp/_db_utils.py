from sqlite3 import Connection

from .db import SCHEMA_VERSION


class StaleIndexError(RuntimeError):
    """Raised when a tool depends on schema introduced in a later release than
    the one used to populate the indexed DB. Typically means the user upgraded
    the package but hasn't run `legistar-mcp index --full` to backfill."""


def _check_table_populated(
    conn: Connection, table: str, related_table: str
) -> None:
    """Raise StaleIndexError if the user upgraded code (SCHEMA_VERSION) without
    re-running --full (user_version stayed at an older value).

    Silent when:
    - PRAGMA user_version >= SCHEMA_VERSION (DB matches code).
    - The DB has never been indexed at all (related table empty —
      legitimately empty, not stale).

    `table` and `related_table` are kept for diagnostic message clarity even
    though the trigger condition no longer depends on row counts. The old
    row-count heuristic conflated "stale" with "legitimately empty" — e.g.,
    an archive with resolutions-only bills (no votes) or events with no
    matter-id items would hit a false positive that --full couldn't fix.
    """
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version >= SCHEMA_VERSION:
        return  # DB is current — never stale.
    # Don't bother people whose DB is just freshly initialized (no data yet).
    has_data = conn.execute(f"SELECT 1 FROM {related_table} LIMIT 1").fetchone()
    if not has_data:
        return
    raise StaleIndexError(
        f"DB schema version is {current_version}, code expects {SCHEMA_VERSION}. "
        f"The `{table}` table may be missing or incompletely populated. "
        f"Run `legistar-mcp index --full` to backfill."
    )
