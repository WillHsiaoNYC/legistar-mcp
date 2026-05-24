from sqlite3 import Connection

_ALLOWED_FIELDS = {
    "status_name": ("bills", "status_name"),
    "type_name": ("bills", "type_name"),
    "body_name": ("bills", "body_name"),
    "event_committee": ("events", "body_name"),
}


def list_vocabulary(conn: Connection, field: str) -> list[str]:
    """Return distinct non-null values for a known DB column.

    Lets the agent discover the exact spelling of statuses, types, committees,
    etc., so it doesn't have to guess (avoids 'Enacted' vs 'Enacted (Mayor's
    Desk for Signature)' confusion).

    Does NOT include agency vocabulary — call agencies.yaml directly or use
    `search_bills(agency=...)` (the resolver accepts aliases case-insensitively).
    """
    if field not in _ALLOWED_FIELDS:
        raise ValueError(
            f"unknown field {field!r}; allowed: {sorted(_ALLOWED_FIELDS)}"
        )
    table, col = _ALLOWED_FIELDS[field]
    rows = conn.execute(
        f"SELECT DISTINCT {col} FROM {table} "
        f"WHERE {col} IS NOT NULL ORDER BY {col}"
    ).fetchall()
    return [r[0] for r in rows]
