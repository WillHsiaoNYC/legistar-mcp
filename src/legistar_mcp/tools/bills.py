from sqlite3 import Connection


def search_bills(
    conn: Connection,
    query: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    status: str | None = None,
    type: str | None = None,
    committee: str | None = None,
    sponsor_slug: str | None = None,
    limit: int = 20,
) -> list[dict]:
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

    return [dict(r) for r in conn.execute(sql, params).fetchall()]
