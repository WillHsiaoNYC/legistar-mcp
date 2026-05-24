from sqlite3 import Connection


class StaleIndexError(RuntimeError):
    """Raised when a tool depends on a table that exists but hasn't been
    populated. Typically means the user upgraded code adding a new schema
    object but hasn't run `legistar-mcp index --full` to backfill."""


def _check_table_populated(
    conn: Connection, table: str, related_table: str
) -> None:
    """Raise StaleIndexError if `table` is empty but `related_table` has rows.

    Silent (returns None) when:
    - `table` has at least one row (data is present)
    - BOTH tables are empty (genuinely empty DB; not our concern)

    Safe to call on every tool invocation — at most one short index lookup.
    """
    has_data = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
    if has_data:
        return
    has_related = conn.execute(f"SELECT 1 FROM {related_table} LIMIT 1").fetchone()
    if not has_related:
        return
    raise StaleIndexError(
        f"The `{table}` table is empty but `{related_table}` is populated. "
        f"This usually means you upgraded the package but haven't run "
        f"`legistar-mcp index --full` since. Run a full reindex to backfill."
    )
