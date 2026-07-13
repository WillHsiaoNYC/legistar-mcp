import datetime as _dt
import json
from pathlib import Path
from sqlite3 import Connection

from ._aggregate import fts_join, run_aggregate, year_window
from ._snippet import _archive_root, _build_snippet, _extract_phrases
from ._validate import build_fts_query, clamp_limit, validate_days, validate_year

# Fields searched for snippet context. Matches the FTS column set, with
# "text" mapped to the source JSON's "Text" key.
_SNIPPET_FIELDS: tuple[tuple[str, str], ...] = (
    ("Title", "title"),
    ("Summary", "summary"),
    ("Text", "Text"),
)


def _legistar_url(bill_id: int | None) -> str | None:
    """Build a public Legistar URL for the matter, or None if missing the id.

    Uses the InSite gateway because our source `ID` is the matter "key" used
    by gateway.aspx, NOT the numeric id LegislationDetail.aspx?ID= expects
    (those are a different, larger id space). Legistar resolves the gateway
    redirect to the canonical detail page server-side.
    """
    if not bill_id:
        return None
    return f"https://legistar.council.nyc.gov/gateway.aspx?m=l&id=/matter.aspx?key={bill_id}"


def _bill_filters(
    query: str | None,
    year_from: int | None,
    year_to: int | None,
    status: str | None,
    type: str | None,
    committee: str | None,
) -> tuple[list[str], list[str], list]:
    """JOIN/WHERE/params shared by search_bills and aggregate_bills so a
    filter fix lands in both. The sponsor_slug filter stays at the call sites:
    its join flavor differs (search wants INNER; aggregate must reuse the
    LEFT JOIN it adds for sponsor_slug grouping). Returns (joins, where,
    params) with where and params in matching order.
    """
    joins: list[str] = []
    where: list[str] = []
    params: list = []
    if query:
        jc, match = fts_join("bills", "bill_id")
        joins += jc
        where.append(match)
        params.append(query)
    yclauses, yparams = year_window("bills.intro_date", year_from, year_to)
    where += yclauses
    params += yparams
    if status:
        where.append("bills.status_name = ?")
        params.append(status)
    if type:
        where.append("bills.type_name = ?")
        params.append(type)
    if committee:
        where.append("bills.body_name = ?")
        params.append(committee)
    return joins, where, params


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
    limit = clamp_limit(limit)
    year_from = validate_year("year_from", year_from)
    year_to = validate_year("year_to", year_to)
    fts_query = build_fts_query(conn, "bills", query, agency)

    joins, where, params = _bill_filters(fts_query, year_from, year_to, status, type, committee)
    if sponsor_slug:
        joins.append("JOIN sponsors s ON bills.id = s.bill_id")
        where.append("s.person_slug = ?")
        params.append(sponsor_slug)

    sql = (
        "SELECT DISTINCT bills.id, bills.guid, bills.file, bills.title, bills.summary, "
        "bills.status_name, bills.type_name, bills.body_name, bills.intro_date "
        "FROM bills"
    )
    if joins:
        sql += " " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY bills.intro_date DESC LIMIT ?"
    params.append(limit)

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r.pop("guid", None)
        r["legistar_url"] = _legistar_url(r.get("id"))

    if agency and rows:
        phrases = _extract_phrases(fts_query or "")
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
    bill["LegistarURL"] = _legistar_url(bill.get("ID"))
    return bill


# intro_year cast to INTEGER so callers don't get string years that sort
# lexically. sponsor_slug requires the sponsors LEFT JOIN added below.
_BILL_DIM_EXPRS = {
    "status_name": "bills.status_name",
    "type_name": "bills.type_name",
    "body_name": "bills.body_name",
    "sponsor_slug": "s.person_slug",
    "intro_year": "CAST(substr(bills.intro_date, 1, 4) AS INTEGER)",
}
# intro_year is undefined for a NULL intro_date, and sponsor_slug is NULL for
# every unsponsored bill (the LEFT JOIN makes that structural) — exclude those
# rows so callers never get a spurious {dim: None} bucket.
_BILL_NON_NULL_COLS = {
    "intro_year": "bills.intro_date",
    "sponsor_slug": "s.person_slug",
}


def aggregate_bills(
    conn: Connection,
    group_by: list[str],
    query: str | None = None,
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
    intro_year. Filters mirror search_bills (year/agency/status/etc.). Shares
    its query engine with aggregate_events (see tools/_aggregate.run_aggregate).

    Note on interactions: passing sponsor_slug as both a filter and a
    group_by dimension will produce a single-row aggregate (filtered to that
    one slug). Passing agency triggers an FTS5 join that may slow large
    aggregations; bound results with `limit`.
    """
    year_from = validate_year("year_from", year_from)
    year_to = validate_year("year_to", year_to)
    fts_query = build_fts_query(conn, "bills", query, agency)
    joins, where, params = _bill_filters(fts_query, year_from, year_to, status, type, committee)
    if "sponsor_slug" in group_by or sponsor_slug:
        joins.append("LEFT JOIN sponsors s ON bills.id = s.bill_id")
    if sponsor_slug:
        where.append("s.person_slug = ?")
        params.append(sponsor_slug)

    return run_aggregate(
        conn,
        table="bills",
        dim_exprs=_BILL_DIM_EXPRS,
        group_by=group_by,
        joins=joins,
        where=where,
        params=params,
        limit=limit,
        non_null_cols=_BILL_NON_NULL_COLS,
    )


def recent_bills(
    conn: Connection,
    days: int = 7,
    status: str | None = None,
    type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Bills introduced within the last `days` days. Convenience wrapper — does
    NOT take an `agency` filter; use search_bills(agency=...) for that.

    Note: there is no upper bound on intro_date. A bill with a future or
    typo'd IntroDate will be returned and will sort to the top of results
    (ORDER BY intro_date DESC). Use search_bills(year_to=...) for a
    precise bounded window.
    """
    limit = clamp_limit(limit)
    days = validate_days(days)
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
        r.pop("guid", None)
        r["legistar_url"] = _legistar_url(r.get("id"))
    return rows
