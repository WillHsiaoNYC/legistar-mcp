import json
from pathlib import Path
from sqlite3 import Connection


def search_people(
    conn: Connection,
    name: str | None = None,
    active_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    where: list[str] = []
    params: list = []
    if name:
        where.append("LOWER(full_name) LIKE ?")
        params.append(f"%{name.lower()}%")
    if active_only:
        where.append("is_active = 1")
    sql = "SELECT slug, full_name, is_active, start_date, end_date FROM people"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY full_name LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_person(conn: Connection, archive_root: Path, slug: str) -> dict | None:
    row = conn.execute("SELECT path FROM people WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    with open(Path(archive_root) / row["path"], encoding="utf-8") as f:
        person = json.load(f)
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
