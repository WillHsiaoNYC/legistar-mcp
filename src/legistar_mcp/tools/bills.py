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
        where.append("bills.intro_date <= ?")
        params.append(f"{year_to}-12-31")
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
        "SELECT DISTINCT bills.id, bills.file, bills.title, bills.summary, "
        "bills.status_name, bills.type_name, bills.body_name, bills.intro_date "
        "FROM bills" + join
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY bills.intro_date DESC LIMIT ?"
    params.append(limit)

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

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
        return json.load(f)
