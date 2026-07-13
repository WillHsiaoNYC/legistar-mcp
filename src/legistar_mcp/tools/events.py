import datetime as _dt
import json
from pathlib import Path
from sqlite3 import Connection

from .._db_utils import _check_table_populated
from ._aggregate import date_upper_bound, fts_join, run_aggregate
from ._snippet import _archive_root, _build_snippet, _extract_phrases
from ._validate import (
    build_fts_query,
    clamp_limit,
    load_archive_json,
    resolve_bill_id,
    today_nyc,
    validate_days,
    validate_iso_date,
)
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


def _event_filters(
    query: str | None,
    date_from: str | None,
    date_to: str | None,
    committee: str | None,
) -> tuple[list[str], list[str], list]:
    """JOIN/WHERE/params shared by search_events and aggregate_events so a
    filter fix lands in both. Returns (joins, where, params) with where and
    params in matching order.
    """
    joins: list[str] = []
    where: list[str] = []
    params: list = []
    if query:
        jc, match = fts_join("events", "event_id")
        joins += jc
        where.append(match)
        params.append(query)
    if date_from:
        where.append("events.date >= ?")
        params.append(date_from)
    if date_to:
        # date stores full ISO timestamps; a bare YYYY-MM-DD covers the whole
        # day so date_to='2024-08-15' includes "2024-08-15T13:30:00-04:00".
        clause, param = date_upper_bound("events.date", date_to)
        where.append(clause)
        params.append(param)
    if committee:
        where.append("events.body_name = ?")
        params.append(committee)
    return joins, where, params


def search_events(
    conn: Connection,
    query: str | None = None,
    agency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    committee: str | None = None,
    limit: int = 20,
) -> list[dict]:
    limit = clamp_limit(limit)
    date_from = validate_iso_date("date_from", date_from)
    date_to = validate_iso_date("date_to", date_to)
    fts_query = build_fts_query(conn, "events", query, agency)

    joins, where, params = _event_filters(fts_query, date_from, date_to, committee)

    sql = (
        "SELECT DISTINCT events.id, events.insite_url, events.body_name, events.date, events.location "
        "FROM events"
    )
    if joins:
        sql += " " + " ".join(joins)
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
        phrases = _extract_phrases(fts_query or "")
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


def get_event(conn: Connection, archive_root: Path, id: int) -> dict:
    row = conn.execute("SELECT path FROM events WHERE id = ?", (id,)).fetchone()
    if not row:
        raise ValueError(
            f"No event with id {id}. Find events via search_events or upcoming_events."
        )
    event = load_archive_json(archive_root, row["path"])
    event["LegistarURL"] = event.get("InSiteURL")
    return event


def upcoming_events(
    conn: Connection,
    days: int = 14,
    committee: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Events in the next `days` days. Same row shape as search_events."""
    limit = clamp_limit(limit)
    days = validate_days(days)
    today = today_nyc()
    # The last in-window day is today + days; date_upper_bound turns it into
    # the exclusive next-day bound so full ISO timestamps on that day (e.g.
    # "2024-08-15T13:30:00-04:00") still match the lex compare.
    last_day = (today + _dt.timedelta(days=days)).isoformat()
    clause, cutoff = date_upper_bound("events.date", last_day)
    sql = (
        "SELECT events.id, events.insite_url, events.body_name, events.date, events.location "
        f"FROM events WHERE events.date >= ? AND {clause}"
    )
    params: list = [today.isoformat(), cutoff]
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
    limit = clamp_limit(limit)
    _check_table_populated(conn, "event_items", "events")

    bill_id = resolve_bill_id(conn, file, id)

    sql = (
        "SELECT events.id, events.insite_url, events.body_name, events.date, "
        "events.location, ei.item_title, ei.item_sequence, ei.action_name "
        "FROM event_items ei JOIN events ON ei.event_id = events.id "
        "WHERE ei.bill_id = ?"
    )
    params: list = [bill_id]
    if only_upcoming:
        sql += " AND events.date >= ?"
        params.append(today_nyc().isoformat())
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


_EVENT_DIM_EXPRS = {
    "body_name": "events.body_name",
    "event_year": "CAST(substr(events.date, 1, 4) AS INTEGER)",
    "event_month": "substr(events.date, 1, 7)",
}
# event_year / event_month are undefined for a NULL date — exclude those rows
# so callers never get a spurious {'event_year': None} bucket.
_EVENT_NON_NULL_COLS = {"event_year": "events.date", "event_month": "events.date"}


def aggregate_events(
    conn: Connection,
    group_by: list[str],
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    committee: str | None = None,
    agency: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Group events by one or more dimensions and return per-group counts.

    Allowed group_by values: body_name, event_year, event_month. Filters mirror
    search_events (date_from/date_to/committee/agency). Shares its query engine
    with aggregate_bills (see tools/_aggregate.run_aggregate) — useful for
    answering "which committees held the most hearings in <year>?" in one
    round-trip.

    A bare YYYY-MM-DD date_to covers the whole day (events store full ISO
    timestamps), so date_to='2024-12-31' counts every hearing on Dec 31.
    """
    date_from = validate_iso_date("date_from", date_from)
    date_to = validate_iso_date("date_to", date_to)
    fts_query = build_fts_query(conn, "events", query, agency)
    joins, where, params = _event_filters(fts_query, date_from, date_to, committee)

    return run_aggregate(
        conn,
        table="events",
        dim_exprs=_EVENT_DIM_EXPRS,
        group_by=group_by,
        joins=joins,
        where=where,
        params=params,
        limit=limit,
        non_null_cols=_EVENT_NON_NULL_COLS,
    )


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
