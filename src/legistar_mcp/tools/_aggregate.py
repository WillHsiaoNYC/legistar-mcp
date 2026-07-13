"""Shared building blocks for the GROUP BY count tools (aggregate_bills /
aggregate_events) and the date/year predicates they share with search_* and
list_committees.

Extracted so the two aggregators stop being hand-synced near-clones: a fix to
the ORDER BY tie-break, the LIMIT placement, the COUNT(DISTINCT) semantics, or
a date-boundary rule lands in one place instead of diverging between copies.
"""
import datetime as _dt
from sqlite3 import Connection

from ._validate import clamp_limit


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
    nonsensical — is never silently dropped. Years are zero-padded to 4 digits:
    unpadded "999-01-01" lex-sorts AFTER "2024-..." and would silently exclude
    everything. year_to >= 9999 emits no upper clause at all — no ISO date can
    exceed it, and "10000-01-01" lex-sorts BEFORE every real date. Returns
    (clauses, params) to splice into a WHERE list.
    """
    clauses: list[str] = []
    params: list = []
    if year_from is not None:
        clauses.append(f"{col} >= ?")
        params.append(f"{year_from:04d}-01-01")
    if year_to is not None and year_to < 9999:
        clauses.append(f"{col} < ?")
        params.append(f"{year_to + 1:04d}-01-01")
    return clauses, params


def date_upper_bound(col: str, date_to: str) -> tuple[str, str]:
    """Inclusive `date_to` predicate for a column storing full ISO timestamps.

    A bare prefix covers the *whole* period it names, via an exclusive
    next-period bound — otherwise the lex compare excludes the very rows the
    caller asked for (`col <= '2024-08-15'` drops "2024-08-15T13:30:00-04:00";
    `col <= '2024-08'` drops all of August):

    - ``YYYY-MM-DD`` → `col < {next day}`
    - ``YYYY-MM``    → `col < {first of next month}`
    - ``YYYY``       → `col < {next Jan 1}`

    A full timestamp, or a bound past year 9999 (where no next-period boundary
    is representable — date.max + 1 day raises OverflowError, not ValueError),
    is compared directly with `<=`. Malformed prefixes are rejected upstream by
    validate_iso_date, so they raise here rather than silently falling back.
    Returns (clause, param).
    """
    try:
        if len(date_to) == 10:
            nxt = _dt.date.fromisoformat(date_to) + _dt.timedelta(days=1)
            return f"{col} < ?", nxt.isoformat()
        if len(date_to) == 7:
            first = _dt.date.fromisoformat(date_to + "-01")
            nxt = (first.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
            return f"{col} < ?", nxt.isoformat()
        if len(date_to) == 4 and date_to.isdigit() and int(date_to) < 9999:
            return f"{col} < ?", f"{int(date_to) + 1:04d}-01-01"
    except OverflowError:
        pass  # year 9999: no next-period boundary is representable
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

    Joins fan one base row out into many (FTS map: one per matching FTS item;
    sponsors: one per sponsor), so any join forces `COUNT(DISTINCT {table}.id)`.
    The join-free path counts plain rows — no fan-out is possible and COUNT(*)
    lets SQLite skip per-group distinct tracking. Results are ordered by count
    desc with the grouping columns as a stable tie-break.
    """
    limit = clamp_limit(limit, hi=1000)
    validate_group_by(group_by, set(dim_exprs))
    where = list(where)  # local copy — never mutate the caller's list
    if non_null_cols:
        for g in group_by:
            col = non_null_cols.get(g)
            if col:
                where.append(f"{col} IS NOT NULL")
    count_expr = f"COUNT(DISTINCT {table}.id)" if joins else "COUNT(*)"
    select_cols = [f"{dim_exprs[g]} AS {g}" for g in group_by]
    sql = (
        f"SELECT {', '.join(select_cols)}, {count_expr} AS count "
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
