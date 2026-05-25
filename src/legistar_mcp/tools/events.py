import datetime as _dt
import json
from pathlib import Path
from sqlite3 import Connection

from .._db_utils import _check_table_populated
from ..agency import resolve_to_fts_query
from ._snippet import _archive_root, _build_snippet, _extract_phrases, _get_agencies
from .bills import _legistar_url as _legistar_url_bill

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


# Event public URLs come from the source JSON's `InSiteURL` field. We tried
# constructing MeetingDetail.aspx URLs from our API ID/GUID — the web detail
# page uses different identifiers (LEGID/GID/G with a separate web-side GUID),
# so the constructed links resolved to "Invalid parameters!". The source data
# already ships the correct link; we just store and surface it.


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
        "SELECT DISTINCT events.id, events.insite_url, events.body_name, events.date, events.location "
        "FROM events" + join
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY events.date DESC LIMIT ?"
    params.append(limit)

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["legistar_url"] = r.pop("insite_url", None)

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
        event = json.load(f)
    event["LegistarURL"] = event.get("InSiteURL")
    return event


def upcoming_events(
    conn: Connection,
    days: int = 14,
    committee: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Events in the next `days` days. Same row shape as search_events."""
    today = _dt.date.today().isoformat()
    # Exclusive upper bound: cutoff is the first day OUTSIDE the window, so
    # events.date values that start with the last in-window day (full ISO
    # timestamps like "2024-08-15T13:30:00-04:00") still match. A lex compare
    # against a date-only cutoff would otherwise drop events on the cutoff day.
    cutoff = (_dt.date.today() + _dt.timedelta(days=days + 1)).isoformat()
    sql = (
        "SELECT events.id, events.insite_url, events.body_name, events.date, events.location "
        "FROM events WHERE events.date >= ? AND events.date < ?"
    )
    params: list = [today, cutoff]
    if committee:
        sql += " AND events.body_name = ?"
        params.append(committee)
    sql += " ORDER BY events.date ASC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["legistar_url"] = r.pop("insite_url", None)
    return rows


def get_bill_hearings(
    conn: Connection,
    file: str | None = None,
    id: int | None = None,
    only_upcoming: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Events where the given bill was on the agenda. Raises StaleIndexError
    if the event_items table is empty post-upgrade (run `--full` to fix)."""
    _check_table_populated(conn, "event_items", "events")

    if file:
        row = conn.execute("SELECT id FROM bills WHERE file = ?", (file,)).fetchone()
        bill_id = row["id"] if row else None
    elif id is not None:
        bill_id = id
    else:
        raise ValueError("Must supply either `file` or `id`")
    if bill_id is None:
        return []

    sql = (
        "SELECT events.id, events.insite_url, events.body_name, events.date, "
        "events.location, ei.item_title, ei.item_sequence, ei.action_name "
        "FROM event_items ei JOIN events ON ei.event_id = events.id "
        "WHERE ei.bill_id = ?"
    )
    params: list = [bill_id]
    if only_upcoming:
        sql += " AND events.date >= ?"
        params.append(_dt.date.today().isoformat())
        # "Next hearing" semantics: nearest-future first. When only_upcoming
        # is False the caller is browsing history, so most-recent-first
        # (DESC) is the sensible default for that branch.
        sql += " ORDER BY events.date ASC, ei.item_sequence ASC LIMIT ?"
    else:
        sql += " ORDER BY events.date DESC, ei.item_sequence ASC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["legistar_url"] = r.pop("insite_url", None)
    return rows


_EVENT_ALLOWED_GROUP_BY = {"body_name", "event_year", "event_month"}


def aggregate_events(
    conn: Connection,
    group_by: list[str],
    date_from: str | None = None,
    date_to: str | None = None,
    committee: str | None = None,
    agency: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Group events by one or more dimensions and return per-group counts.

    Allowed group_by values: body_name, event_year, event_month. Filters mirror
    search_events (date_from/date_to/committee/agency). Mirror of
    aggregate_bills for the events table — useful for answering "which
    committees held the most hearings in <year>?" in one round-trip.

    Date comparison uses lex order on stored ISO timestamps; pass full-day
    strings for date_to with care (matches search_events semantics).
    """
    if not group_by:
        raise ValueError("group_by must contain at least one dimension")
    for g in group_by:
        if g not in _EVENT_ALLOWED_GROUP_BY:
            raise ValueError(
                f"unsupported group_by dimension: {g!r}. "
                f"Allowed: {sorted(_EVENT_ALLOWED_GROUP_BY)}"
            )

    expr = {
        "body_name": "events.body_name",
        "event_year": "CAST(substr(events.date, 1, 4) AS INTEGER)",
        "event_month": "substr(events.date, 1, 7)",
    }
    select_cols = [f"{expr[g]} AS {g}" for g in group_by]

    where, params, joins = [], [], []
    if agency:
        query = resolve_to_fts_query(agency, _get_agencies())
        joins.append("JOIN events_fts_map m ON events.id = m.event_id")
        joins.append("JOIN events_fts f ON m.fts_rowid = f.rowid")
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

    # COUNT(DISTINCT) because agency mode joins events_fts_map, which
    # produces one row per matching event item — without DISTINCT a
    # council meeting with 5 NYPD-mentioning items would be counted 5x.
    sql = (
        f"SELECT {', '.join(select_cols)}, COUNT(DISTINCT events.id) AS count "
        f"FROM events {' '.join(joins)}"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY {', '.join(group_by)}"
    sql += " ORDER BY count DESC, " + ", ".join(group_by)
    sql += " LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_event_bills(conn: Connection, event_id: int) -> list[dict]:
    """Bills on the agenda for a specific event. Raises StaleIndexError if
    the event_items table is empty post-upgrade."""
    _check_table_populated(conn, "event_items", "events")

    sql = (
        "SELECT bills.id, bills.file, bills.title, bills.status_name, "
        "ei.item_title, ei.item_sequence, ei.action_name "
        "FROM event_items ei JOIN bills ON ei.bill_id = bills.id "
        "WHERE ei.event_id = ? ORDER BY ei.item_sequence ASC"
    )
    rows = [dict(r) for r in conn.execute(sql, (event_id,)).fetchall()]
    for r in rows:
        r["legistar_url"] = _legistar_url_bill(r.get("id"))
    return rows
