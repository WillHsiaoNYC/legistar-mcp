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

# Bound the per-event snippet list. A council meeting with 100+ Items can
# otherwise return thousands of duplicate snippets when an alias-rich agency
# is mentioned in every agenda item.
_MAX_MENTIONS_PER_EVENT = 5


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
    # A council meeting can have 100+ Items × 3 fields × N alias phrases; without
    # dedupe + cap, one search response could carry 10k+ near-identical snippets.
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
            seen: set[tuple[str, str]] = set()
            rel = path_rows.get(r["id"])
            if root and rel and phrases:
                try:
                    with open(root / rel, encoding="utf-8") as f:
                        data = json.load(f) or {}
                except (FileNotFoundError, OSError):
                    data = None
                if data is not None:
                    for item in data.get("Items") or []:
                        if len(mentions) >= _MAX_MENTIONS_PER_EVENT:
                            break
                        for field_label, key in _SNIPPET_FIELDS:
                            value = item.get(key) or ""
                            snip = _build_snippet(value, phrases)
                            if not snip:
                                continue
                            sig = (field_label, snip)
                            if sig in seen:
                                continue
                            seen.add(sig)
                            mentions.append({"field": field_label, "snippet": snip})
                            if len(mentions) >= _MAX_MENTIONS_PER_EVENT:
                                break
            r["mentions"] = mentions

    return rows


def get_event(conn: Connection, archive_root: Path, id: int) -> dict | None:
    row = conn.execute("SELECT path FROM events WHERE id = ?", (id,)).fetchone()
    if not row:
        return None
    with open(Path(archive_root) / row["path"], encoding="utf-8") as f:
        return json.load(f)
