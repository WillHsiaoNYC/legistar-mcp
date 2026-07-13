from sqlite3 import Connection


class StaleIndexError(RuntimeError):
    """Raised when a tool depends on schema introduced in a later release than
    the one used to populate the indexed DB. Typically means the user upgraded
    the package but hasn't run `legistar-mcp index --full` to backfill."""


def _check_table_populated(
    conn: Connection, table: str, related_table: str, min_version: int
) -> None:
    """Raise StaleIndexError if the DB was last fully indexed before the release
    that introduced `table`.

    `min_version` is the SCHEMA_VERSION that first shipped `table`. The gate is
    per-feature, not global: a DB fully indexed at `min_version` (or later) is
    complete for `table` even after the code's SCHEMA_VERSION has moved on for
    unrelated reasons — a v3-complete DB must not be told its votes table is
    broken just because an unrelated v4/v5 bump happened.

    Silent when:
    - PRAGMA user_version >= min_version (the release that introduced `table`
      has been fully indexed).
    - The DB has never been indexed at all (related table empty —
      legitimately empty, not stale).
    """
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version >= min_version:
        return  # the release that introduced `table` has been fully indexed
    # Don't bother people whose DB is just freshly initialized (no data yet).
    has_data = conn.execute(f"SELECT 1 FROM {related_table} LIMIT 1").fetchone()
    if not has_data:
        return
    raise StaleIndexError(
        f"DB is at version {current_version}, `{table}` requires a full index "
        f"from version {min_version}+. Run `legistar-mcp index --full` to backfill."
    )
