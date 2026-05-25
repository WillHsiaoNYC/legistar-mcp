from sqlite3 import Connection


def list_committees(conn: Connection) -> list[dict]:
    """All committees with bill/event counts and first-seen dates.

    `first_bill_date` / `first_event_date` are the earliest dates this body
    appears on a bill or event in the indexed archive — a proxy for "when did
    this committee start showing up?", NOT an official establishment date.
    Committees that pre-date the archive (1996) or that were renamed will
    show a misleading-looking earliest date; treat as a lower bound.
    """
    # UNION ALL portable to SQLite 3.7+. FULL OUTER JOIN would be cleaner
    # but isn't available before SQLite 3.39, and CPython on Linux uses the
    # system libsqlite3 (Ubuntu 22.04 ships 3.37, RHEL 9 ships 3.34) — so we
    # can't rely on it given our requires-python>=3.11 declaration.
    # MAX() over (date, NULL) returns the date because SQL aggregates skip
    # NULLs, so the outer MAX coalesces the two branches' per-table MIN()s.
    sql = """
        SELECT
            name,
            MAX(bill_count) AS bill_count,
            MAX(event_count) AS event_count,
            MAX(first_bill_date) AS first_bill_date,
            MAX(first_event_date) AS first_event_date
        FROM (
            SELECT body_name AS name, COUNT(*) AS bill_count, 0 AS event_count,
                   MIN(intro_date) AS first_bill_date, NULL AS first_event_date
            FROM bills WHERE body_name IS NOT NULL
            GROUP BY body_name
            UNION ALL
            SELECT body_name AS name, 0 AS bill_count, COUNT(*) AS event_count,
                   NULL AS first_bill_date, MIN(date) AS first_event_date
            FROM events WHERE body_name IS NOT NULL
            GROUP BY body_name
        )
        GROUP BY name
        ORDER BY (bill_count + event_count) DESC, name ASC
    """
    return [dict(r) for r in conn.execute(sql).fetchall()]
