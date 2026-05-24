from sqlite3 import Connection

from .._db_utils import _check_table_populated


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


def get_voting_record(
    conn: Connection,
    slug: str,
    year_from: int | None = None,
    year_to: int | None = None,
    vote_value: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Every vote cast by `slug`, optionally filtered by year and outcome.
    Raises StaleIndexError if the votes table is empty post-upgrade."""
    _check_table_populated(conn, "votes", "bills")

    sql = (
        "SELECT v.vote_value, v.vote_date, v.event_id, v.bill_id, "
        "       v.action, v.passed_flag, "
        "       b.file, b.title, b.status_name "
        "FROM votes v LEFT JOIN bills b ON v.bill_id = b.id "
        "WHERE v.person_slug = ?"
    )
    params: list = [slug]
    if year_from:
        sql += " AND v.vote_date >= ?"
        params.append(f"{year_from}-01-01")
    if year_to:
        # v.vote_date stores full ISO timestamps; a date-only inclusive upper
        # would lex-exclude Dec 31 votes. Use next-year-Jan-1 exclusive.
        sql += " AND v.vote_date < ?"
        params.append(f"{year_to + 1}-01-01")
    if vote_value:
        sql += " AND v.vote_value = ?"
        params.append(vote_value)
    sql += " ORDER BY v.vote_date DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def vote_breakdown(conn: Connection, bill_id: int, limit: int = 100) -> list[dict]:
    """Every council member's vote on a specific bill, across all history records.

    Returns rows with seven columns:
      - person_slug
      - full_name (NULL if no people row indexed)
      - vote_value (e.g. 'Affirmative', 'Negative', 'Abstain')
      - vote_date
      - event_id (NULL for filing actions or history entries without an event)
      - action (e.g. 'Approved by Committee')
      - passed_flag (0/1 indicating whether the action passed)

    Rows with NULL vote_date (rare history entries) are placed at the END of
    the result rather than the top: SQLite's default DESC ordering puts NULLs
    first, which is the opposite of what callers expect for "most-recent first".

    Limit defaults to 100; pass higher for omnibus bills with many vote rows.

    Raises StaleIndexError if the votes table is empty post-upgrade (run
    `--full` to backfill).
    """
    _check_table_populated(conn, "votes", "bills")

    # `v.vote_date IS NULL` is 0 for not-null and 1 for null, so adding it as
    # the FIRST ORDER BY key pushes null-dated rows to the end. Then the
    # secondary `v.vote_date DESC` orders the not-null rows newest-first.
    sql = (
        "SELECT v.person_slug, p.full_name, v.vote_value, v.vote_date, "
        "       v.event_id, v.action, v.passed_flag "
        "FROM votes v LEFT JOIN people p ON v.person_slug = p.slug "
        "WHERE v.bill_id = ? "
        "ORDER BY v.vote_date IS NULL, v.vote_date DESC, p.full_name ASC NULLS LAST "
        "LIMIT ?"
    )
    return [dict(r) for r in conn.execute(sql, (bill_id, limit)).fetchall()]
