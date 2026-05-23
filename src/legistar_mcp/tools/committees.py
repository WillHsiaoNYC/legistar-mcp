from sqlite3 import Connection


def list_committees(conn: Connection) -> list[dict]:
    # UNION ALL portable to SQLite 3.7+. FULL OUTER JOIN would be cleaner
    # but isn't available before SQLite 3.39, and CPython on Linux uses the
    # system libsqlite3 (Ubuntu 22.04 ships 3.37, RHEL 9 ships 3.34) — so we
    # can't rely on it given our requires-python>=3.11 declaration.
    sql = """
        SELECT
            name,
            MAX(bill_count) AS bill_count,
            MAX(event_count) AS event_count
        FROM (
            SELECT body_name AS name, COUNT(*) AS bill_count, 0 AS event_count
            FROM bills WHERE body_name IS NOT NULL
            GROUP BY body_name
            UNION ALL
            SELECT body_name AS name, 0 AS bill_count, COUNT(*) AS event_count
            FROM events WHERE body_name IS NOT NULL
            GROUP BY body_name
        )
        GROUP BY name
        ORDER BY (bill_count + event_count) DESC, name ASC
    """
    return [dict(r) for r in conn.execute(sql).fetchall()]
