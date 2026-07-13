from sqlite3 import Connection

from ._snippet import _get_agencies
from ._validate import envelope

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

    Does NOT include agency vocabulary — use the list_agencies tool or
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


def list_agencies(query: str | None = None) -> dict:
    """The agency dictionary behind search_bills/search_events `agency=`:
    95 hand-curated NYC agencies with the aliases the resolver accepts.
    Previously invisible to agents, who had to guess agency spellings."""
    entries = [
        {"slug": slug, "display": e["display"], "aliases": e.get("aliases", [e["display"]])}
        for slug, e in _get_agencies().items()
    ]
    if query:
        q = query.strip().lower()
        entries = [
            a for a in entries
            if q in a["slug"].lower()
            or q in a["display"].lower()
            or any(q in al.lower() for al in a["aliases"])
        ]
    entries.sort(key=lambda a: a["slug"])
    return envelope(entries, total=len(entries))
