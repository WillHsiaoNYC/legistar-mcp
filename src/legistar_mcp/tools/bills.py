import datetime as _dt
import json
from pathlib import Path
from sqlite3 import Connection

from ..agency import resolve_to_fts_query
from ._snippet import _archive_root, _build_snippet, _extract_phrases, _get_agencies

# Fields searched for snippet context. Matches the FTS column set, with
# "text" mapped to the source JSON's "Text" key.
_SNIPPET_FIELDS: tuple[tuple[str, str], ...] = (
    ("Title", "title"),
    ("Summary", "summary"),
    ("Text", "Text"),
)


def _legistar_url(bill_id: int | None, guid: str | None) -> str | None:
    """Build the public Legistar LegislationDetail URL, or None if missing parts."""
    if not bill_id or not guid:
        return None
    return f"https://legistar.council.nyc.gov/LegislationDetail.aspx?ID={bill_id}&GUID={guid}"


def search_bills(
    conn: Connection,
    query: str | None = None,
    agency: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    status: str | None = None,
    type: str | None = None,
    committee: str | None = None,
    sponsor_slug: str | None = None,
    limit: int = 20,
) -> list[dict]:
    if agency:
        query = resolve_to_fts_query(agency, _get_agencies())

    where: list[str] = []
    params: list = []
    join = ""

    if query:
        join = (
            " JOIN bills_fts_map m ON bills.id = m.bill_id"
            " JOIN bills_fts f ON m.fts_rowid = f.rowid"
        )
        where.append("bills_fts MATCH ?")
        params.append(query)
    if year_from:
        where.append("bills.intro_date >= ?")
        params.append(f"{year_from}-01-01")
    if year_to:
        # intro_date stores full ISO timestamps like "2024-12-31T00:00:00Z";
        # a date-only inclusive upper bound would lex-exclude Dec 31 entries.
        # Use the first day of the next year as an exclusive upper bound.
        where.append("bills.intro_date < ?")
        params.append(f"{year_to + 1}-01-01")
    if status:
        where.append("bills.status_name = ?")
        params.append(status)
    if type:
        where.append("bills.type_name = ?")
        params.append(type)
    if committee:
        where.append("bills.body_name = ?")
        params.append(committee)
    if sponsor_slug:
        join += " JOIN sponsors s ON bills.id = s.bill_id"
        where.append("s.person_slug = ?")
        params.append(sponsor_slug)

    sql = (
        "SELECT DISTINCT bills.id, bills.guid, bills.file, bills.title, bills.summary, "
        "bills.status_name, bills.type_name, bills.body_name, bills.intro_date "
        "FROM bills" + join
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY bills.intro_date DESC LIMIT ?"
    params.append(limit)

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["legistar_url"] = _legistar_url(r.get("id"), r.pop("guid", None))

    if agency and rows:
        phrases = _extract_phrases(query) if query else []
        root = _archive_root(conn)
        path_rows = {
            r["id"]: r["path"]
            for r in conn.execute(
                f"SELECT id, path FROM bills WHERE id IN ({','.join('?' * len(rows))})",
                [r["id"] for r in rows],
            ).fetchall()
        }
        for r in rows:
            mentions: list[dict] = []
            rel = path_rows.get(r["id"])
            if root and rel and phrases:
                # If the archive moved or a file was deleted since indexing,
                # degrade to empty mentions rather than 500'ing the whole search.
                try:
                    with open(root / rel, encoding="utf-8") as f:
                        data = json.load(f) or {}
                except (FileNotFoundError, OSError):
                    data = None
                if data is not None:
                    for field_label, key in _SNIPPET_FIELDS:
                        value = data.get(key) or ""
                        snip = _build_snippet(value, phrases)
                        if snip:
                            mentions.append({"field": field_label, "snippet": snip})
            r["mentions"] = mentions

    return rows


def get_bill(
    conn: Connection,
    archive_root: Path,
    file: str | None = None,
    id: int | None = None,
) -> dict | None:
    if file:
        row = conn.execute("SELECT path FROM bills WHERE file = ?", (file,)).fetchone()
    elif id is not None:
        row = conn.execute("SELECT path FROM bills WHERE id = ?", (id,)).fetchone()
    else:
        raise ValueError("Must supply either `file` or `id`")
    if not row:
        return None
    with open(Path(archive_root) / row["path"], encoding="utf-8") as f:
        bill = json.load(f)
    bill["LegistarURL"] = _legistar_url(bill.get("ID"), bill.get("GUID"))
    return bill


_ALLOWED_GROUP_BY = {
    "status_name", "type_name", "body_name", "sponsor_slug", "intro_year",
}


def aggregate_bills(
    conn: Connection,
    group_by: list[str],
    year_from: int | None = None,
    year_to: int | None = None,
    status: str | None = None,
    type: str | None = None,
    committee: str | None = None,
    sponsor_slug: str | None = None,
    agency: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Group bills by the requested dimensions and return per-group counts.

    Allowed group_by values: status_name, type_name, body_name, sponsor_slug,
    intro_year. Filters mirror search_bills (year/agency/status/etc.).

    Note on interactions: passing sponsor_slug as both a filter and a
    group_by dimension will produce a single-row aggregate (filtered to that
    one slug). Passing agency triggers an FTS5 join that may slow large
    aggregations; bound results with `limit`.
    """
    if not group_by:
        raise ValueError("group_by must contain at least one dimension")
    for g in group_by:
        if g not in _ALLOWED_GROUP_BY:
            raise ValueError(
                f"unsupported group_by dimension: {g!r}. "
                f"Allowed: {sorted(_ALLOWED_GROUP_BY)}"
            )

    # Expression per dimension. intro_year cast to INTEGER so callers don't
    # get string years that sort lexically.
    expr = {
        "status_name": "bills.status_name",
        "type_name": "bills.type_name",
        "body_name": "bills.body_name",
        "sponsor_slug": "s.person_slug",
        "intro_year": "CAST(substr(bills.intro_date, 1, 4) AS INTEGER)",
    }
    select_cols = [f"{expr[g]} AS {g}" for g in group_by]

    where, params, joins = [], [], []
    if "sponsor_slug" in group_by:
        joins.append("LEFT JOIN sponsors s ON bills.id = s.bill_id")
    if agency:
        query = resolve_to_fts_query(agency, _get_agencies())
        joins.append("JOIN bills_fts_map m ON bills.id = m.bill_id")
        joins.append("JOIN bills_fts f ON m.fts_rowid = f.rowid")
        where.append("bills_fts MATCH ?")
        params.append(query)
    if year_from:
        where.append("bills.intro_date >= ?")
        params.append(f"{year_from}-01-01")
    if year_to:
        # See search_bills above — exclusive upper bound by next-year-Jan-1
        # so Dec 31 ISO timestamps aren't lex-excluded.
        where.append("bills.intro_date < ?")
        params.append(f"{year_to + 1}-01-01")
    if status:
        where.append("bills.status_name = ?")
        params.append(status)
    if type:
        where.append("bills.type_name = ?")
        params.append(type)
    if committee:
        where.append("bills.body_name = ?")
        params.append(committee)
    if sponsor_slug:
        if "sponsor_slug" not in group_by:
            joins.append("LEFT JOIN sponsors s ON bills.id = s.bill_id")
        where.append("s.person_slug = ?")
        params.append(sponsor_slug)

    sql = (
        f"SELECT {', '.join(select_cols)}, COUNT(DISTINCT bills.id) AS count "
        f"FROM bills {' '.join(joins)}"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY {', '.join(group_by)}"
    sql += " ORDER BY count DESC, " + ", ".join(group_by)
    sql += " LIMIT ?"
    params.append(limit)

    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def recent_bills(
    conn: Connection,
    days: int = 7,
    status: str | None = None,
    type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Bills introduced within the last `days`. Convenience wrapper — does
    NOT take an `agency` filter; use search_bills(agency=...) for that."""
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    sql = (
        "SELECT DISTINCT bills.id, bills.guid, bills.file, bills.title, "
        "bills.summary, bills.status_name, bills.type_name, bills.body_name, "
        "bills.intro_date FROM bills WHERE bills.intro_date >= ?"
    )
    params: list = [cutoff]
    if status:
        sql += " AND bills.status_name = ?"
        params.append(status)
    if type:
        sql += " AND bills.type_name = ?"
        params.append(type)
    sql += " ORDER BY bills.intro_date DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["legistar_url"] = _legistar_url(r.get("id"), r.pop("guid", None))
    return rows
