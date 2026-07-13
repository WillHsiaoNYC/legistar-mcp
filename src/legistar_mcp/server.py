"""MCP stdio server wiring the Legistar tools.

Reads `LEGISTAR_DB_PATH` from the environment at startup and fails fast if it
is missing or doesn't exist. The archive root is read from the indexed DB
itself (`index_state.archive_root`, written by `legistar-mcp index`) so the
search-tools and the detail-tools can't drift apart if a user re-points an
env var between indexing and serving.
"""

from __future__ import annotations

import functools
import os
import threading
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .db import open_db
from .tools._snippet import _archive_root
from .tools.bills import aggregate_bills as _aggregate_bills
from .tools.bills import get_bill as _get_bill
from .tools.bills import get_bill_text as _get_bill_text
from .tools.bills import recent_bills as _recent_bills
from .tools.bills import search_bills as _search_bills
from .tools.committees import list_committees as _list_committees
from .tools.events import aggregate_events as _aggregate_events
from .tools.events import get_bill_hearings as _get_bill_hearings
from .tools.events import get_event as _get_event
from .tools.events import get_event_bills as _get_event_bills
from .tools.events import search_events as _search_events
from .tools.events import upcoming_events as _upcoming_events
from .tools.people import get_person as _get_person
from .tools.people import search_people as _search_people
from .tools.relationships import co_sponsors as _co_sponsors
from .tools.relationships import get_voting_record as _get_voting_record
from .tools.relationships import vote_breakdown as _vote_breakdown
from .tools.status import data_status as _data_status
from .tools.vocab import list_agencies as _list_agencies
from .tools.vocab import list_vocabulary as _list_vocabulary

# Every tool is read-only (never mutates the archive) and closed-world (never
# reaches outside the local index). Declaring it once and reusing lets MCP
# clients label the tools and skip write-confirmation prompts.
_RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

# Reusable param annotations for the byte-identical paging params — a wording
# tweak lands once instead of at 8 call sites. Tools with tailored paging or
# limit text (e.g. search_bills' offset, the 1-1000 limits) stay inline.
_Offset = Annotated[int, Field(description="Rows to skip for paging.")]
_Limit200 = Annotated[int, Field(description="Max results, clamped to 1-200.")]

# MCP-exposed enum constraints — keep in sync with tools/vocab.py
# `_ALLOWED_FIELDS` and tools/bills.py `aggregate_bills` group_by validation.
# Surfacing these as Literal lets FastMCP's JSON-schema generator publish the
# enum to the agent, so invalid values fail fast at the protocol layer
# instead of bubbling up as runtime ValueError from the tool body.
VocabField = Literal["status_name", "type_name", "body_name", "event_committee"]
GroupByDim = Literal[
    "status_name", "type_name", "body_name", "sponsor_slug", "intro_year"
]
EventGroupByDim = Literal["body_name", "event_year", "event_month"]

# Module-level lock around the shared sqlite Connection. sqlite3 forbids
# concurrent use of one Connection across threads even with
# check_same_thread=False; FastMCP may dispatch tools from a worker thread.
# Wrapping each tool body in `with _db_lock` serializes access without forcing
# every caller to reopen the DB. Uncontended in the current single-thread
# stdio transport, so the overhead is a no-op atomic.
_db_lock = threading.Lock()


def _load_env_db_path() -> Path:
    """Return db_path from LEGISTAR_DB_PATH, failing fast if invalid."""
    db_path_str = os.environ.get("LEGISTAR_DB_PATH")
    if not db_path_str:
        raise RuntimeError(
            "LEGISTAR_DB_PATH is not set. Run `legistar-mcp index` first and "
            "point LEGISTAR_DB_PATH at the resulting SQLite file."
        )
    db_path = Path(db_path_str)
    if not db_path.exists():
        raise RuntimeError(f"LEGISTAR_DB_PATH does not exist: {db_path}")
    return db_path


def make_server() -> FastMCP:
    """Construct a FastMCP server with the Legistar tools registered.

    Resolves the DB and archive_root eagerly so misconfiguration surfaces at
    startup, not on the first tool call.
    """
    db_path = _load_env_db_path()
    conn = open_db(db_path)

    archive_root = _archive_root(conn)
    if archive_root is None:
        raise RuntimeError(
            "DB does not record an archive_root. Re-run `legistar-mcp index` "
            "to populate it."
        )
    if not archive_root.exists() or not archive_root.is_dir():
        raise RuntimeError(
            f"archive_root recorded in DB does not exist or is not a directory: "
            f"{archive_root}. Re-run `legistar-mcp index --archive <path>` if "
            "you moved the archive."
        )

    server = FastMCP(
        "legistar-mcp",
        instructions=(
            "NYC City Council legislation: bills, hearings/events, council "
            "members, and roll-call votes, indexed locally from the "
            "jehiah/nyc_legislation archive (1998-present). Read-only.\n"
            "- Call data_status first when freshness matters: the index is "
            "only as new as the user's last `legistar-mcp index` run.\n"
            "- Discover exact filter spellings with list_vocabulary "
            "(statuses, types, committees) and list_agencies (95 NYC "
            "agencies with aliases); string filters match case-insensitively.\n"
            "- For agency questions prefer search_bills/search_events with "
            "agency=… — it expands aliases and returns role-context "
            "`mentions` snippets. Combine with query=… to narrow.\n"
            "- List tools (except list_vocabulary) return {results, total, "
            "offset, truncated}; page with offset. Bill and event rows carry "
            "legistar_url — cite it.\n"
            "- Empty `mentions` on an FTS hit means stemmed (non-literal) "
            "match, not a false positive; use get_bill_text for the passage."
        ),
    )

    def _db_locked(fn):
        """Serialize tool bodies on the shared sqlite Connection.

        Applied below each `@server.tool()` so FastMCP still sees the original
        signature (preserved via functools.wraps) for JSON-schema generation.
        """
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with _db_lock:
                return fn(*args, **kwargs)
        return wrapper

    @server.tool(annotations=_RO)
    @_db_locked
    def search_bills(
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Free-text search over bill name/title/summary/full text. "
                    "Plain words or quoted phrases; FTS5 operators (OR, NEAR) "
                    "allowed. Combines (AND) with agency."
                )
            ),
        ] = None,
        agency: Annotated[
            str | None,
            Field(
                description=(
                    "NYC agency name, acronym, or alias (e.g. 'NYPD', "
                    "'Department of Consumer Affairs'). Resolved against "
                    "list_agencies; adds role-context `mentions` snippets to "
                    "each hit."
                )
            ),
        ] = None,
        year_from: Annotated[
            int | None,
            Field(description="Earliest intro year, inclusive. 4-digit, e.g. 2022."),
        ] = None,
        year_to: Annotated[
            int | None,
            Field(description="Latest intro year, inclusive. 4-digit, e.g. 2024."),
        ] = None,
        status: Annotated[
            str | None,
            Field(
                description=(
                    "Exact status name, case-insensitive (e.g. 'Enacted'). "
                    "Discover values via list_vocabulary('status_name')."
                )
            ),
        ] = None,
        type: Annotated[
            str | None,
            Field(
                description=(
                    "Exact bill type, case-insensitive (e.g. 'Introduction', "
                    "'Resolution'). Discover via list_vocabulary('type_name')."
                )
            ),
        ] = None,
        committee: Annotated[
            str | None,
            Field(
                description=(
                    "Exact committee (body) name, case-insensitive. "
                    "Discover via list_vocabulary('body_name')."
                )
            ),
        ] = None,
        sponsor_slug: Annotated[
            str | None,
            Field(
                description=(
                    "Council-member slug (e.g. 'adrienne-e-adams'). "
                    "Find via search_people."
                )
            ),
        ] = None,
        limit: _Limit200 = 20,
        offset: Annotated[
            int,
            Field(description="Rows to skip for paging; use with `total` from a prior call."),
        ] = 0,
    ) -> dict:
        """Search NYC Council bills. Returns {results, total, offset, truncated}; each result has file, title, summary, status, type, committee, intro_date, legistar_url, and — when agency/query is set — `mentions` snippets quoting the matching statutory text."""
        return _search_bills(
            conn,
            query=query,
            agency=agency,
            year_from=year_from,
            year_to=year_to,
            status=status,
            type=type,
            committee=committee,
            sponsor_slug=sponsor_slug,
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def get_bill(
        file: Annotated[
            str | None,
            Field(description="Bill file number, e.g. 'Int 0153-2022' or 'Res 0021-2024'."),
        ] = None,
        id: Annotated[
            int | None,
            Field(
                description=(
                    "Numeric Legistar matter ID (the `id` field from "
                    "search_bills results)."
                )
            ),
        ] = None,
    ) -> dict:
        """Fetch one bill's full source record (sponsors, history, attachments, votes, full text). Responses can be large — for just the statutory text around a phrase, prefer get_bill_text. Supply `file` or `id`; unknown identifiers raise an error naming the fix."""
        return _get_bill(conn, archive_root, file=file, id=id)

    @server.tool(annotations=_RO)
    @_db_locked
    def get_bill_text(
        file: Annotated[
            str | None,
            Field(description="Bill file number, e.g. 'Int 0153-2022'."),
        ] = None,
        id: Annotated[int | None, Field(description="Numeric bill ID.")] = None,
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Literal phrase to locate (case-insensitive). Omit to "
                    "get the head of the text."
                )
            ),
        ] = None,
        context_chars: Annotated[
            int,
            Field(description="Characters of context on each side of a match (100-5000)."),
        ] = 1500,
        max_matches: Annotated[
            int,
            Field(description="Max match windows to return (1-20)."),
        ] = 5,
    ) -> dict:
        """Extract passages from a bill's full statutory text without fetching the whole record: windows around each occurrence of `query`, or the head of the text if no query. Returns total_chars/truncated so you know how much text exists beyond the segments."""
        return _get_bill_text(
            conn, archive_root, file=file, id=id, query=query,
            context_chars=context_chars, max_matches=max_matches,
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def search_people(
        name: Annotated[
            str | None,
            Field(
                description=(
                    "Name words, any order; all must appear (e.g. 'Adrienne "
                    "Adams' matches 'Adrienne E. Adams')."
                )
            ),
        ] = None,
        active_only: Annotated[
            bool,
            Field(description="True = only currently serving members."),
        ] = False,
        limit: _Limit200 = 20,
        offset: _Offset = 0,
    ) -> dict:
        """Search council members. Returns {results, total, offset, truncated}; each result has slug, full_name, is_active, start/end dates. Slugs feed get_person, search_bills(sponsor_slug), get_voting_record, co_sponsors."""
        return _search_people(
            conn, name=name, active_only=active_only, limit=limit, offset=offset
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def get_person(
        slug: Annotated[
            str,
            Field(description="Member slug from search_people, e.g. 'adrienne-e-adams'."),
        ],
    ) -> dict:
        """Fetch a council member's full profile plus sponsored-bill counts grouped by bill status (under `_stats`)."""
        return _get_person(conn, archive_root, slug)

    @server.tool(annotations=_RO)
    @_db_locked
    def search_events(
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Free-text search over agenda item titles and "
                    "agenda/minutes notes. Combines (AND) with agency."
                )
            ),
        ] = None,
        agency: Annotated[
            str | None,
            Field(
                description=(
                    "NYC agency name or alias; adds `mentions` snippets "
                    "from matching agenda items."
                )
            ),
        ] = None,
        date_from: Annotated[
            str | None,
            Field(description="Earliest event date, ISO: YYYY, YYYY-MM, or YYYY-MM-DD."),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(
                description=(
                    "Latest event date, inclusive of the whole named "
                    "period. ISO: YYYY, YYYY-MM, or YYYY-MM-DD."
                )
            ),
        ] = None,
        committee: Annotated[
            str | None,
            Field(
                description=(
                    "Exact committee (body) name, case-insensitive. "
                    "Discover via list_vocabulary('event_committee')."
                )
            ),
        ] = None,
        limit: _Limit200 = 20,
        offset: _Offset = 0,
    ) -> dict:
        """Search committee hearings and Council meetings. Returns {results, total, offset, truncated}; each result has id, body_name, date, location, legistar_url, and `mentions` when agency/query is set."""
        return _search_events(
            conn,
            query=query,
            agency=agency,
            date_from=date_from,
            date_to=date_to,
            committee=committee,
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def get_event(
        id: Annotated[
            int,
            Field(description="Numeric event ID from search_events/upcoming_events results."),
        ],
    ) -> dict:
        """Fetch one event's full source record: agenda items, minutes notes, per-item actions and votes. Large for full Council meetings."""
        return _get_event(conn, archive_root, id)

    @server.tool(annotations=_RO)
    @_db_locked
    def list_committees(
        year_from: Annotated[
            int | None,
            Field(
                description=(
                    "Restrict counts to bills introduced / events held "
                    "from this year, inclusive."
                )
            ),
        ] = None,
        year_to: Annotated[
            int | None,
            Field(description="Restrict counts through this year, inclusive."),
        ] = None,
    ) -> dict:
        """All committees with bill/event counts and first-seen dates (earliest activity in the archive — a lower bound, not an establishment date). Returns {results, total, offset, truncated} sorted by activity."""
        return _list_committees(conn, year_from=year_from, year_to=year_to)

    @server.tool(annotations=_RO)
    @_db_locked
    def aggregate_bills(
        group_by: Annotated[
            list[GroupByDim],
            Field(
                description=(
                    "Dimensions to group by, e.g. ['intro_year'] or "
                    "['status_name','intro_year']. Empty list = one "
                    "grand-total row."
                )
            ),
        ],
        query: Annotated[
            str | None,
            Field(description="Free-text filter, same semantics as search_bills.query."),
        ] = None,
        year_from: Annotated[
            int | None,
            Field(description="Earliest intro year, inclusive (4-digit)."),
        ] = None,
        year_to: Annotated[
            int | None,
            Field(description="Latest intro year, inclusive (4-digit)."),
        ] = None,
        status: Annotated[
            str | None,
            Field(
                description=(
                    "Exact status name, case-insensitive; see "
                    "list_vocabulary('status_name')."
                )
            ),
        ] = None,
        type: Annotated[
            str | None,
            Field(
                description=(
                    "Exact bill type, case-insensitive; see "
                    "list_vocabulary('type_name')."
                )
            ),
        ] = None,
        committee: Annotated[
            str | None,
            Field(
                description=(
                    "Exact committee name, case-insensitive; see "
                    "list_vocabulary('body_name')."
                )
            ),
        ] = None,
        sponsor_slug: Annotated[
            str | None,
            Field(description="Council-member slug; find via search_people."),
        ] = None,
        agency: Annotated[
            str | None,
            Field(description="NYC agency name or alias (FTS join — slower on broad windows)."),
        ] = None,
        limit: Annotated[int, Field(description="Max groups returned, clamped to 1-1000.")] = 100,
        offset: Annotated[int, Field(description="Groups to skip for paging.")] = 0,
    ) -> dict:
        """Group bills by one or more dimensions (status_name, type_name, body_name, sponsor_slug, intro_year) and return per-group counts, largest first. Filters: query, agency, year_from/year_to, status, type, committee, sponsor_slug."""
        return _aggregate_bills(
            conn,
            group_by=group_by,
            query=query,
            year_from=year_from,
            year_to=year_to,
            status=status,
            type=type,
            committee=committee,
            sponsor_slug=sponsor_slug,
            agency=agency,
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def aggregate_events(
        group_by: Annotated[
            list[EventGroupByDim],
            Field(
                description=(
                    "Dimensions to group by: body_name, event_year, "
                    "event_month (YYYY-MM). Empty list = one grand-total row."
                )
            ),
        ],
        query: Annotated[
            str | None,
            Field(description="Free-text filter, same semantics as search_events.query."),
        ] = None,
        date_from: Annotated[
            str | None,
            Field(description="Earliest event date, ISO: YYYY, YYYY-MM, or YYYY-MM-DD."),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(description="Latest event date, inclusive of the whole named period."),
        ] = None,
        committee: Annotated[
            str | None,
            Field(description="Exact committee name, case-insensitive."),
        ] = None,
        agency: Annotated[
            str | None,
            Field(description="NYC agency name or alias (FTS join — slower on broad windows)."),
        ] = None,
        limit: Annotated[int, Field(description="Max groups returned, clamped to 1-1000.")] = 100,
        offset: Annotated[int, Field(description="Groups to skip for paging.")] = 0,
    ) -> dict:
        """Group events by one or more dimensions (body_name, event_year, event_month) and return per-group counts, largest first. Filters: query, agency, date_from/date_to, committee."""
        return _aggregate_events(
            conn,
            group_by=group_by,
            query=query,
            date_from=date_from,
            date_to=date_to,
            committee=committee,
            agency=agency,
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def list_vocabulary(
        field: Annotated[
            VocabField,
            Field(
                description=(
                    "Which column's distinct values to list: status_name / "
                    "type_name / body_name (bills) or event_committee (events)."
                )
            ),
        ],
    ) -> list[str]:
        """Every distinct value for a filter column — use before filtering by status/type/committee to get exact spellings. Complete list, no paging. For agencies use list_agencies instead."""
        return _list_vocabulary(conn, field=field)

    @server.tool(annotations=_RO)
    @_db_locked
    def list_agencies(
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Substring filter over slug, display name, and aliases "
                    "(e.g. 'police', 'consumer')."
                )
            ),
        ] = None,
    ) -> dict:
        """The 95 NYC agencies the `agency=` parameter understands, with slug, display name, and accepted aliases. Call this before agency-filtered searches when unsure of a name; unmatched agency strings fall back to literal phrase search."""
        return _list_agencies(query=query)

    @server.tool(annotations=_RO)
    @_db_locked
    def data_status() -> dict:
        """Index freshness and coverage: when the local index was last built, how many bills/events/people/votes it holds, the date range covered, and any warnings (stale index, schema behind). Call this first when a question depends on recent or upcoming items."""
        return _data_status(conn)

    @server.tool(annotations=_RO)
    @_db_locked
    def recent_bills(
        days: Annotated[
            int,
            Field(
                description=(
                    "Window size in days back from today (NYC time). "
                    "Positive integer."
                )
            ),
        ] = 7,
        status: Annotated[
            str | None,
            Field(description="Exact status name, case-insensitive."),
        ] = None,
        type: Annotated[
            str | None,
            Field(description="Exact bill type, case-insensitive."),
        ] = None,
        limit: _Limit200 = 20,
        offset: _Offset = 0,
    ) -> dict:
        """Bills introduced in the last `days` days, newest first. Includes a `warning` field when the local index looks stale. For agency-scoped searches use search_bills(agency=…)."""
        return _recent_bills(
            conn, days=days, status=status, type=type, limit=limit, offset=offset
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def upcoming_events(
        days: Annotated[
            int,
            Field(
                description=(
                    "Window size in days ahead from today (NYC time). "
                    "Positive integer."
                )
            ),
        ] = 14,
        committee: Annotated[
            str | None,
            Field(description="Exact committee (body) name, case-insensitive."),
        ] = None,
        limit: _Limit200 = 20,
        offset: _Offset = 0,
    ) -> dict:
        """Events scheduled in the next `days` days, soonest first. Includes a `warning` field when the local index looks stale — a stale index can miss newly scheduled hearings."""
        return _upcoming_events(
            conn, days=days, committee=committee, limit=limit, offset=offset
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def co_sponsors(
        slug: Annotated[str, Field(description="Member slug from search_people.")],
        min_overlap: Annotated[
            int,
            Field(
                description=(
                    "Only return members who co-sponsored at least this "
                    "many bills together."
                )
            ),
        ] = 5,
        limit: _Limit200 = 20,
        offset: _Offset = 0,
    ) -> dict:
        """Council members who most often co-sponsor bills with the given member, sorted by shared-bill count."""
        return _co_sponsors(
            conn, slug=slug, min_overlap=min_overlap, limit=limit, offset=offset
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def get_bill_hearings(
        file: Annotated[
            str | None,
            Field(description="Bill file number, e.g. 'Int 0153-2022'."),
        ] = None,
        id: Annotated[
            int | None,
            Field(description="Numeric bill ID."),
        ] = None,
        only_upcoming: Annotated[
            bool,
            Field(
                description=(
                    "True = only future events, soonest first; False = "
                    "full history, newest first."
                )
            ),
        ] = False,
        limit: _Limit200 = 20,
        offset: _Offset = 0,
    ) -> dict:
        """Events where the given bill was on the agenda, with per-item action names. Supply `file` or `id`."""
        return _get_bill_hearings(
            conn, file=file, id=id, only_upcoming=only_upcoming, limit=limit, offset=offset
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def get_event_bills(
        event_id: Annotated[
            int,
            Field(description="Numeric event ID from search_events/upcoming_events."),
        ],
    ) -> dict:
        """Bills on a specific event's agenda in agenda order, each with item title, sequence, action, and legistar_url."""
        return _get_event_bills(conn, event_id=event_id)

    @server.tool(annotations=_RO)
    @_db_locked
    def get_voting_record(
        slug: Annotated[str, Field(description="Member slug from search_people.")],
        year_from: Annotated[
            int | None,
            Field(description="Earliest vote year, inclusive (4-digit)."),
        ] = None,
        year_to: Annotated[
            int | None,
            Field(description="Latest vote year, inclusive (4-digit)."),
        ] = None,
        vote_value: Annotated[
            str | None,
            Field(
                description=(
                    "Filter to one outcome: 'Affirmative', 'Negative', "
                    "'Absent', 'Abstain', 'Excused', …"
                )
            ),
        ] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-1000.")] = 100,
        offset: _Offset = 0,
    ) -> dict:
        """Every vote the member cast, newest first, with the bill's file/title/status and the action voted on."""
        return _get_voting_record(
            conn,
            slug=slug,
            year_from=year_from,
            year_to=year_to,
            vote_value=vote_value,
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=_RO)
    @_db_locked
    def vote_breakdown(
        bill_id: Annotated[int | None, Field(description="Numeric bill ID.")] = None,
        file: Annotated[
            str | None,
            Field(description="Bill file number, e.g. 'Int 0153-2022'."),
        ] = None,
        limit: Annotated[
            int,
            Field(description="Max results, clamped to 1-1000. Raise for omnibus bills."),
        ] = 100,
        offset: _Offset = 0,
    ) -> dict:
        """Every council member's vote on one bill across all its roll calls, newest action first. Supply `bill_id` or `file`."""
        return _vote_breakdown(conn, bill_id=bill_id, file=file, limit=limit, offset=offset)

    return server


async def main() -> None:
    """Boot the MCP server over stdio."""
    server = make_server()
    await server.run_stdio_async()
