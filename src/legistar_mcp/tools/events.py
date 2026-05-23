import json
from pathlib import Path
from sqlite3 import Connection

from ..agency import resolve_to_fts_query
from ._snippet import _archive_root, _build_snippet, _extract_phrases, _get_agencies

# events_fts column order: item_title (0), agenda_note (1), minutes_note (2).
# When building snippets server-side we map JSON keys to display labels.
_SNIPPET_FIELDS: tuple[tuple[str, str], ...] = (
    ("Title", "Title"),
    ("AgendaNote", "AgendaNote"),
    ("MinutesNote", "MinutesNote"),
)


def search_events(
    conn: Connection,
    query: str | None = None,
    agency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    committee: str | None = None,
    limit: int = 20,
) -> list[dict]:
    if agency:
        query = resolve_to_fts_query(agency, _get_agencies())

    where: list[str] = []
    params: list = []
    join = ""

    if query:
        join = (
            " JOIN events_fts_map m ON events.id = m.event_id"
            " JOIN events_fts f ON m.fts_rowid = f.rowid"
        )
        where.append("events_fts MATCH ?")
        params.append(query)
    if date_from:
        where.append("events.date >= ?")
        params.append(date_from)
    if date_to:
        where.append("events.date <= ?")
        params.append(date_to)
    if committee:
        where.append("events.body_name = ?")
        params.append(committee)

    sql = (
        "SELECT DISTINCT events.id, events.body_name, events.date, events.location "
        "FROM events" + join
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY events.date DESC LIMIT ?"
    params.append(limit)

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # Agency mode: build per-event mentions by reading source JSON for each match.
    if agency and rows:
        phrases = _extract_phrases(query) if query else []
        root = _archive_root(conn)
        ids = [r["id"] for r in rows]
        path_rows = {
            r["id"]: r["path"]
            for r in conn.execute(
                f"SELECT id, path FROM events WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        }
        for r in rows:
            mentions: list[dict] = []
            rel = path_rows.get(r["id"])
            if root and rel and phrases:
                try:
                    with open(root / rel, encoding="utf-8") as f:
                        data = json.load(f) or {}
                except (FileNotFoundError, OSError):
                    data = None
                if data is not None:
                    for item in data.get("Items") or []:
                        for field_label, key in _SNIPPET_FIELDS:
                            value = item.get(key) or ""
                            snip = _build_snippet(value, phrases)
                            if snip:
                                mentions.append({"field": field_label, "snippet": snip})
            r["mentions"] = mentions

    return rows
