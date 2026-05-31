"""Shared building blocks for the GROUP BY count tools (aggregate_bills /
aggregate_events) and the date/year predicates they share with search_* and
list_committees.

Extracted so the two aggregators stop being hand-synced near-clones: a fix to
the ORDER BY tie-break, the LIMIT placement, the COUNT(DISTINCT) semantics, or
a date-boundary rule lands in one place instead of diverging between copies.
"""
import datetime as _dt
from sqlite3 import Connection


def fts_join(table: str, id_col: str) -> tuple[list[str], str]:
    """FTS5 join clauses + MATCH predicate for `{table}`.

    `{table}_fts_map` links each base row to its FTS rows and `{table}_fts`
    holds the indexed text; the caller binds the query string to the MATCH `?`.
    Shared by all four agency/keyword paths (search_bills, search_events,
    aggregate_bills, aggregate_events) so the join wiring lives in one place.
    Returns (join_clauses, match_clause).
    """
    return (
        [
            f"JOIN {table}_fts_map m ON {table}.id = m.{id_col}",
            f"JOIN {table}_fts f ON m.fts_rowid = f.rowid",
        ],
        f"{table}_fts MATCH ?",
    )


def validate_group_by(group_by: list[str], allowed: set[str]) -> None:
    """Raise ValueError if `group_by` is empty or names a dimension not in
    `allowed`. Shared by every aggregate_* tool so the error surface is one
    thing, not one-per-tool."""
    if not group_by:
        raise ValueError("group_by must contain at least one dimension")
    for g in group_by:
        if g not in allowed:
            raise ValueError(
                f"unsupported group_by dimension: {g!r}. "
                f"Allowed: {sorted(allowed)}"
            )


def year_window(
    col: str, year_from: int | None, year_to: int | None
) -> tuple[list[str], list]:
    """Inclusive [year_from, year_to] window as ISO predicates on `col`.

    The upper bound is an *exclusive* next-year-Jan-1 (`< {year_to+1}-01-01`)
    so a Dec-31 value stored as a full ISO timestamp ("2024-12-31T23:59:59Z")
    isn't lex-excluded. Uses `is not None` (not truthiness) so year 0 — however
    nonsensical — is never silently dropped. Returns (clauses, params) to splice
    into a WHERE list.
    """
    clauses: list[str] = []
    params: list = []
    if year_from is not None:
        clauses.append(f"{col} >= ?")
        params.append(f"{year_from}-01-01")
    if year_to is not None:
        clauses.append(f"{col} < ?")
        params.append(f"{year_to + 1}-01-01")
    return clauses, params


def date_upper_bound(col: str, date_to: str) -> tuple[str, str]:
    """Inclusive `date_to` predicate for a column storing full ISO timestamps.

    A bare ``YYYY-MM-DD`` is meant to cover the *whole* day, so it becomes an
    exclusive next-day bound (`col < {date_to + 1 day}`) — otherwise
    `col <= '2024-08-15'` lex-excludes same-day rows like
    "2024-08-15T13:30:00-04:00". A full timestamp (or any non-date string) is
    compared directly with `<=`. Returns (clause, param).
    """
    if len(date_to) == 10:
        try:
            nxt = _dt.date.fromisoformat(date_to) + _dt.timedelta(days=1)
            return f"{col} < ?", nxt.isoformat()
        except ValueError:
            pass
    return f"{col} <= ?", date_to


def run_aggregate(
    conn: Connection,
    *,
    table: str,
    dim_exprs: dict[str, str],
    group_by: list[str],
    joins: list[str],
    where: list[str],
    params: list,
    limit: int,
    non_null_cols: dict[str, str] | None = None,
) -> list[dict]:
    """Validate `group_by`, assemble the shared GROUP BY count query, run it.

    `dim_exprs` maps every allowed dimension to its SQL expression and doubles
    as the allow-list (`group_by` is validated against its keys). `non_null_cols`
    maps a dimension to a source column that must be non-NULL for that dimension
    to mean anything — e.g. a year derived from a nullable date. When such a
    dimension is grouped on, rows with a NULL source are excluded, so callers
    never receive a spurious `{dim: None}` bucket.

    Counts `COUNT(DISTINCT {table}.id)` because agency mode joins `{table}_fts_map`
    and fans one base row out into one row per matching FTS item. Results are
    ordered by count desc with the grouping columns as a stable tie-break.
    """
    validate_group_by(group_by, set(dim_exprs))
    where = list(where)  # local copy — never mutate the caller's list
    if non_null_cols:
        for g in group_by:
            col = non_null_cols.get(g)
            if col:
                clause = f"{col} IS NOT NULL"
                if clause not in where:
                    where.append(clause)
    select_cols = [f"{dim_exprs[g]} AS {g}" for g in group_by]
    sql = (
        f"SELECT {', '.join(select_cols)}, COUNT(DISTINCT {table}.id) AS count "
        f"FROM {table}"
    )
    if joins:
        sql += " " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY {', '.join(group_by)}"
    sql += " ORDER BY count DESC, " + ", ".join(group_by)
    sql += " LIMIT ?"
    return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]
