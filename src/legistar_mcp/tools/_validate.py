"""Input validation, FTS-query hygiene, and response envelopes shared by every
MCP tool. Agents routinely send imperfect inputs (negative limits, US-format
dates, punctuated natural-language queries); each helper either coerces the
input to something safe or raises ValueError whose message tells the agent how
to fix the call — never a silent wrong answer.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
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
            # Full timestamp: validate the WHOLE string, not just v[:10], so a
            # bad suffix ('2024-08-15xxxx') can't slip past an ISO-promising
            # validator. Normalize a space separator (str(datetime.now()) form,
            # '2024-08-15 23:59:59') to 'T': stored timestamps use 'T', and
            # space (0x20) < 'T' would lex-exclude the very day named.
            _dt.datetime.fromisoformat(v)
            return v.replace(" ", "T", 1)
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


def resolve_bill_id(conn: Connection, file: str | None, id: int | None) -> int:
    """One identifier contract for every bill-taking tool: accept `file`
    (e.g. 'Int 0153-2022') or numeric `id`, and fail with next-step guidance
    instead of an ambiguous empty result."""
    if file is None and id is None:
        raise ValueError("Supply either `file` (e.g. 'Int 0153-2022') or numeric `id`.")
    if file is not None:
        row = conn.execute("SELECT id FROM bills WHERE file = ?", (file,)).fetchone()
        if row is None:
            raise ValueError(
                f"No bill with file {file!r}. Files look like 'Int 0153-2022' / "
                f"'Res 0021-2024'; find bills via search_bills."
            )
        return row["id"]
    # Verify the numeric id too — otherwise tools fed a hallucinated id return
    # a silent [] instead of the guided error this helper exists to provide.
    if conn.execute("SELECT 1 FROM bills WHERE id = ?", (id,)).fetchone() is None:
        raise ValueError(f"No bill with id {id}. Find bills via search_bills.")
    return id


def require_known_slug(conn: Connection, slug: str) -> None:
    """Distinguish 'unknown person' from 'person with no rows'. Slugs can
    legitimately appear only in sponsors/votes (former members not in the
    people table), so check all three."""
    known = conn.execute(
        "SELECT 1 FROM people WHERE slug = ? "
        "UNION SELECT 1 FROM sponsors WHERE person_slug = ? "
        "UNION SELECT 1 FROM votes WHERE person_slug = ? LIMIT 1",
        (slug, slug, slug),
    ).fetchone()
    if known is None:
        raise ValueError(
            f"Unknown person slug {slug!r}. Find slugs via search_people(name=...)."
        )


def load_archive_json(archive_root: Path, rel_path: str) -> dict:
    """Archive reads for detail tools. The search tools already degrade
    gracefully when a source file vanished after indexing; detail tools were
    500'ing with a bare FileNotFoundError."""
    try:
        with open(Path(archive_root) / rel_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Indexed source file {rel_path!r} is missing or unreadable under "
            f"{archive_root} — the archive was likely moved or pruned after "
            f"indexing. Re-run `legistar-mcp index` to re-sync."
        ) from exc
