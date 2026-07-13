from pathlib import Path
from sqlite3 import Connection

from ._validate import clamp_limit, clamp_offset, envelope, load_archive_json


def search_people(
    conn: Connection,
    name: str | None = None,
    active_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    where: list[str] = []
    params: list = []
    if name:
        for token in name.lower().split():
            where.append("LOWER(full_name) LIKE ?")
            params.append(f"%{token}%")
    if active_only:
        where.append("is_active = 1")
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM people" + where_clause, params).fetchone()[0]
    sql = (
        "SELECT slug, full_name, is_active, start_date, end_date FROM people"
        + where_clause + " ORDER BY full_name LIMIT ? OFFSET ?"
    )
    rows = [dict(r) for r in conn.execute(sql, [*params, limit, offset]).fetchall()]
    return envelope(rows, total, offset)


def get_person(conn: Connection, archive_root: Path, slug: str) -> dict:
    row = conn.execute("SELECT path FROM people WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise ValueError(
            f"Unknown person slug {slug!r}. Find slugs via search_people(name=...)."
        )
    person = load_archive_json(archive_root, row["path"])
    # COALESCE so a NULL status_name doesn't serialize as the string "null"
    # (JSON dict keys must be strings; None becomes "null" via json.dumps).
    stats = dict(
        conn.execute(
            "SELECT COALESCE(b.status_name, '(unknown)') AS status, COUNT(*) AS n "
            "FROM sponsors s JOIN bills b ON s.bill_id = b.id "
            "WHERE s.person_slug = ? "
            "GROUP BY COALESCE(b.status_name, '(unknown)')",
            (slug,),
        ).fetchall()
    )
    person["_stats"] = {"sponsored_bill_count_by_status": stats}
    return person
