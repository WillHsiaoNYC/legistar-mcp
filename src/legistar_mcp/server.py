"""MCP stdio server wiring the 16 Legistar tools.

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
from typing import Literal

from mcp.server.fastmcp import FastMCP

# MCP-exposed enum constraints — keep in sync with tools/vocab.py
# `_ALLOWED_FIELDS` and tools/bills.py `aggregate_bills` group_by validation.
# Surfacing these as Literal lets FastMCP's JSON-schema generator publish the
# enum to the agent, so invalid values fail fast at the protocol layer
# instead of bubbling up as runtime ValueError from the tool body.
VocabField = Literal["status_name", "type_name", "body_name", "event_committee"]
GroupByDim = Literal[
    "status_name", "type_name", "body_name", "sponsor_slug", "intro_year"
]

from .db import open_db

# Module-level lock around the shared sqlite Connection. sqlite3 forbids
# concurrent use of one Connection across threads even with
# check_same_thread=False; FastMCP may dispatch tools from a worker thread.
# Wrapping each tool body in `with _db_lock` serializes access without forcing
# every caller to reopen the DB. Uncontended in the current single-thread
# stdio transport, so the overhead is a no-op atomic.
_db_lock = threading.Lock()
from .tools._snippet import _archive_root
from .tools.bills import aggregate_bills as _aggregate_bills
from .tools.bills import get_bill as _get_bill
from .tools.bills import recent_bills as _recent_bills
from .tools.bills import search_bills as _search_bills
from .tools.committees import list_committees as _list_committees
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
from .tools.vocab import list_vocabulary as _list_vocabulary


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
    """Construct a FastMCP server with all 16 Legistar tools registered.

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

    server = FastMCP("legistar-mcp")

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

    @server.tool()
    @_db_locked
    def search_bills(
        query: str | None = None,
        agency: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        status: str | None = None,
        type: str | None = None,
        committee: str | None = None,
        sponsor_slug: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search NYC Council bills by free-text query, agency, year range, status, type, committee, or sponsor."""
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
        )

    @server.tool()
    @_db_locked
    def get_bill(file: str | None = None, id: int | None = None) -> dict | None:
        """Fetch a single bill's full record by file number (e.g., 'Int 1234-2024') or numeric ID."""
        return _get_bill(conn, archive_root, file=file, id=id)

    @server.tool()
    @_db_locked
    def search_people(
        name: str | None = None,
        active_only: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Search Council members by name; optionally filter to currently active members."""
        return _search_people(conn, name=name, active_only=active_only, limit=limit)

    @server.tool()
    @_db_locked
    def get_person(slug: str) -> dict | None:
        """Fetch a Council member's profile by slug."""
        return _get_person(conn, archive_root, slug)

    @server.tool()
    @_db_locked
    def search_events(
        query: str | None = None,
        agency: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        committee: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search committee hearings/events by query, agency, date range, or committee."""
        return _search_events(
            conn,
            query=query,
            agency=agency,
            date_from=date_from,
            date_to=date_to,
            committee=committee,
            limit=limit,
        )

    @server.tool()
    @_db_locked
    def get_event(id: int) -> dict | None:
        """Fetch a single event's full record by numeric ID."""
        return _get_event(conn, archive_root, id)

    @server.tool()
    @_db_locked
    def list_committees() -> list[dict]:
        """List all committees with bill and event counts."""
        return _list_committees(conn)

    @server.tool()
    @_db_locked
    def aggregate_bills(
        group_by: list[GroupByDim],
        year_from: int | None = None,
        year_to: int | None = None,
        status: str | None = None,
        type: str | None = None,
        committee: str | None = None,
        sponsor_slug: str | None = None,
        agency: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Group bills by one or more dimensions (status_name, type_name, body_name, sponsor_slug, intro_year) and return per-group counts. Supports search_bills filters."""
        return _aggregate_bills(
            conn,
            group_by=group_by,
            year_from=year_from,
            year_to=year_to,
            status=status,
            type=type,
            committee=committee,
            sponsor_slug=sponsor_slug,
            agency=agency,
            limit=limit,
        )

    @server.tool()
    @_db_locked
    def list_vocabulary(field: VocabField) -> list[str]:
        """Return distinct non-null values for a known DB column (status_name, type_name, body_name, event_committee). Helps you discover the exact spelling of statuses, types, and committees."""
        return _list_vocabulary(conn, field=field)

    @server.tool()
    @_db_locked
    def recent_bills(
        days: int = 7,
        status: str | None = None,
        type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Bills introduced within the last `days` days. Convenience wrapper — for agency-scoped searches use search_bills(agency=...) instead."""
        return _recent_bills(conn, days=days, status=status, type=type, limit=limit)

    @server.tool()
    @_db_locked
    def upcoming_events(
        days: int = 14,
        committee: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Events scheduled in the next `days` days. Filter by committee body_name."""
        return _upcoming_events(conn, days=days, committee=committee, limit=limit)

    @server.tool()
    @_db_locked
    def co_sponsors(
        slug: str,
        min_overlap: int = 5,
        limit: int = 20,
    ) -> list[dict]:
        """Council members who have co-sponsored the most bills with a given person (by slug). Returns slug, full_name, and overlap_count, sorted by overlap_count DESC."""
        return _co_sponsors(conn, slug=slug, min_overlap=min_overlap, limit=limit)

    @server.tool()
    @_db_locked
    def get_bill_hearings(
        file: str | None = None,
        id: int | None = None,
        only_upcoming: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Events where a given bill was on the agenda. Supply either bill `file` (e.g., 'Int 0153-2022') or numeric `id`. Set `only_upcoming=True` to filter to future events."""
        return _get_bill_hearings(conn, file=file, id=id, only_upcoming=only_upcoming, limit=limit)

    @server.tool()
    @_db_locked
    def get_event_bills(event_id: int) -> list[dict]:
        """Bills on the agenda for a specific event. Returns rows sorted by item_sequence ascending."""
        return _get_event_bills(conn, event_id=event_id)

    @server.tool()
    @_db_locked
    def get_voting_record(
        slug: str,
        year_from: int | None = None,
        year_to: int | None = None,
        vote_value: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Every vote cast by a council member (by `slug`), optionally filtered by year range and vote_value (e.g., 'Affirmative', 'Negative', 'Absent'). Returns vote_value, vote_date, bill context."""
        return _get_voting_record(
            conn,
            slug=slug,
            year_from=year_from,
            year_to=year_to,
            vote_value=vote_value,
            limit=limit,
        )

    @server.tool()
    @_db_locked
    def vote_breakdown(bill_id: int, limit: int = 100) -> list[dict]:
        """Every council member's vote on a specific bill, sorted most-recent first.

        Returns rows with: person_slug, full_name (NULL if no people row indexed),
        vote_value, vote_date, event_id, action (e.g. 'Approved by Committee'),
        passed_flag (0/1 indicating whether the action passed). NULL-date rows
        (rare) are placed last. Limit defaults to 100; raise it for omnibus
        bills with many vote rows.
        """
        return _vote_breakdown(conn, bill_id=bill_id, limit=limit)

    return server


async def main() -> None:
    """Boot the MCP server over stdio."""
    server = make_server()
    await server.run_stdio_async()
