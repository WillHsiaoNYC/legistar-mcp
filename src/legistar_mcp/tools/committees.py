from sqlite3 import Connection


def list_committees(conn: Connection) -> list[dict]:
    # SQLite 3.39+ supports FULL OUTER JOIN; bundled with Python 3.13 (3.45.x).
    sql = """
        SELECT
            COALESCE(b.name, e.name) AS name,
            COALESCE(b.cnt, 0) AS bill_count,
            COALESCE(e.cnt, 0) AS event_count
        FROM
            (SELECT body_name AS name, COUNT(*) AS cnt FROM bills
             WHERE body_name IS NOT NULL GROUP BY body_name) b
            FULL OUTER JOIN
            (SELECT body_name AS name, COUNT(*) AS cnt FROM events
             WHERE body_name IS NOT NULL GROUP BY body_name) e
            ON b.name = e.name
        ORDER BY (COALESCE(b.cnt, 0) + COALESCE(e.cnt, 0)) DESC, name ASC
    """
    return [dict(r) for r in conn.execute(sql).fetchall()]
