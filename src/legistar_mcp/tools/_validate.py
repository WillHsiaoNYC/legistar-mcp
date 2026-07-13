"""Input validation, FTS-query hygiene, and response envelopes shared by every
MCP tool. Agents routinely send imperfect inputs (negative limits, US-format
dates, punctuated natural-language queries); each helper either coerces the
input to something safe or raises ValueError whose message tells the agent how
to fix the call — never a silent wrong answer.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from sqlite3 import Connection
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..agency import _quote, resolve_to_fts_query
from ._snippet import _get_agencies

_YEAR_LO, _YEAR_HI = 1900, 2100


def clamp_limit(limit: int, hi: int = 200) -> int:
    """SQLite treats a negative LIMIT as unbounded — clamp to [1, hi]."""
    return max(1, min(limit, hi))


def clamp_offset(offset: int) -> int:
    return max(0, offset)


def validate_year(name: str, value: int | None) -> int | None:
    """Years outside a sane window break the lexicographic ISO predicates:
    year_from=24 pads to '0024-01-01' which every real date exceeds (filter
    silently ignored), year_to=24 excludes everything."""
    if value is None:
        return None
    if not _YEAR_LO <= value <= _YEAR_HI:
        raise ValueError(
            f"{name} must be a 4-digit year between {_YEAR_LO} and {_YEAR_HI}, "
            f"got {value!r}. Example: {name}=2024."
        )
    return value


def validate_iso_date(name: str, value: str | None) -> str | None:
    """Accept the ISO prefix forms the date predicates understand: YYYY,
    YYYY-MM, YYYY-MM-DD, or a full ISO timestamp. Anything else (e.g.
    '08/15/2024') would lex-compare meaninglessly against stored ISO dates."""
    if value is None:
        return None
    v = value.strip()
    try:
        if len(v) == 4 and v.isdigit():
            return v
        if len(v) == 7:
            _dt.date.fromisoformat(v + "-01")
            return v
        if len(v) == 10:
            _dt.date.fromisoformat(v)
            return v
        if len(v) > 10:
            _dt.date.fromisoformat(v[:10])
            return v
    except ValueError:
        pass
    raise ValueError(
        f"{name} must be ISO format — YYYY, YYYY-MM, or YYYY-MM-DD "
        f"(e.g. '2024-08-15'), got {value!r}."
    )


def validate_days(days: int) -> int:
    if days < 1:
        raise ValueError(
            f"days must be a positive integer, got {days}. "
            f"Use date-filtered search tools for arbitrary windows."
        )
    return days


def today_nyc() -> _dt.date:
    """The data is NYC government data with -04:00/-05:00 offsets; 'today' for
    windowing must be NYC's calendar day, not the server's (a UTC server after
    ~8pm NYC would otherwise drop tonight's hearings from upcoming_events)."""
    try:
        return _dt.datetime.now(ZoneInfo("America/New_York")).date()
    except ZoneInfoNotFoundError:  # no tz database (bare Windows) — degrade
        return _dt.date.today()


def normalize_fts_query(conn: Connection, fts_table: str, raw: str) -> str | None:
    """Return an FTS5 MATCH string that is guaranteed to parse against
    `fts_table`. Valid syntax passes through (power users keep OR/NEAR/quotes);
    anything FTS5 rejects — 'covid-19', \"don't\", parentheses — is retried as
    one quoted phrase. Whitespace-only means no text filter. Column-filter
    validity differs per table (title: exists on bills_fts, not events_fts),
    hence the live probe instead of a static grammar check."""
    q = raw.strip()
    if not q:
        return None
    try:
        # LIMIT 1, not 0: on SQLite >= 3.43 (this project's floor) the planner
        # short-circuits a LIMIT 0 row-count-zero query and never invokes FTS5's
        # xFilter, so the MATCH string is never parsed and every query — even
        # '(unbalanced' — probes as valid, defeating the quote fallback. LIMIT 1
        # forces one step so a malformed query raises OperationalError here.
        conn.execute(f"SELECT 1 FROM {fts_table} WHERE {fts_table} MATCH ? LIMIT 1", (q,))
        return q
    except sqlite3.OperationalError:
        return _quote(q)


def build_fts_query(
    conn: Connection, table: str, query: str | None, agency: str | None
) -> str | None:
    """Combine the free-text query and the agency alias expansion with AND —
    previously `agency` silently overwrote `query`. `table` is the base table
    name ('bills' or 'events'); the FTS table is f'{table}_fts'."""
    parts: list[str] = []
    if agency:
        parts.append("(" + resolve_to_fts_query(agency, _get_agencies()) + ")")
    if query is not None:
        nq = normalize_fts_query(conn, f"{table}_fts", query)
        if nq:
            parts.append("(" + nq + ")")
    return " AND ".join(parts) if parts else None


def envelope(results: list, total: int, offset: int = 0) -> dict:
    """Uniform list-tool response: agents can tell '20 of 3,412' from
    'exactly 20', and page with offset."""
    return {
        "results": results,
        "total": total,
        "offset": offset,
        "truncated": offset + len(results) < total,
    }
