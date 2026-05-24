from sqlite3 import Connection


def co_sponsors(
    conn: Connection, slug: str, min_overlap: int = 5, limit: int = 20
) -> list[dict]:
    """Return council members who have co-sponsored the most bills with `slug`."""
    sql = """
        SELECT s2.person_slug AS slug,
               COALESCE(p.full_name, s2.person_slug) AS full_name,
               COUNT(DISTINCT s2.bill_id) AS overlap_count
        FROM sponsors s1
        JOIN sponsors s2 ON s1.bill_id = s2.bill_id AND s1.person_slug <> s2.person_slug
        LEFT JOIN people p ON s2.person_slug = p.slug
        WHERE s1.person_slug = ?
        GROUP BY s2.person_slug
        HAVING overlap_count >= ?
        ORDER BY overlap_count DESC, slug ASC   -- slug tiebreaker = deterministic
        LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, (slug, min_overlap, limit)).fetchall()]
