"""MCP stdio server wiring the 7 Legistar tools.

Reads `LEGISTAR_DB_PATH` and `LEGISTAR_ARCHIVE_PATH` from the environment at
startup and fails fast if either is missing or does not exist on disk. The
server opens an existing SQLite index (built via `legistar-mcp index`) — it
will not create or migrate the schema.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .db import open_db
from .tools.bills import get_bill as _get_bill
from .tools.bills import search_bills as _search_bills
from .tools.committees import list_committees as _list_committees
from .tools.events import get_event as _get_event
from .tools.events import search_events as _search_events
from .tools.people import get_person as _get_person
from .tools.people import search_people as _search_people


def _load_env() -> tuple[Path, Path]:
    """Return (db_path, archive_root) from env, failing fast if invalid."""
    db_path_str = os.environ.get("LEGISTAR_DB_PATH")
    archive_str = os.environ.get("LEGISTAR_ARCHIVE_PATH")

    if not db_path_str:
        raise RuntimeError(
            "LEGISTAR_DB_PATH is not set. Run `legistar-mcp index` first and "
            "point LEGISTAR_DB_PATH at the resulting SQLite file."
        )
    if not archive_str:
        raise RuntimeError(
            "LEGISTAR_ARCHIVE_PATH is not set. Set it to the root of the NYC "
            "Legistar archive checkout (the directory containing bills/, "
            "events/, people/)."
        )

    db_path = Path(db_path_str)
    archive_root = Path(archive_str)

    if not db_path.exists():
        raise RuntimeError(f"LEGISTAR_DB_PATH does not exist: {db_path}")
    if not archive_root.exists() or not archive_root.is_dir():
        raise RuntimeError(
            f"LEGISTAR_ARCHIVE_PATH does not exist or is not a directory: {archive_root}"
        )

    return db_path, archive_root


def make_server() -> FastMCP:
    """Construct a FastMCP server with all 7 Legistar tools registered.

    Reads env vars and opens the DB eagerly so misconfiguration surfaces at
    startup, not on the first tool call.
    """
    db_path, archive_root = _load_env()
    conn = open_db(db_path)

    server = FastMCP("legistar-mcp")

    @server.tool()
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
    def get_bill(file: str | None = None, id: int | None = None) -> dict | None:
        """Fetch a single bill's full record by file number (e.g., 'Int 1234-2024') or numeric ID."""
        return _get_bill(conn, archive_root, file=file, id=id)

    @server.tool()
    def search_people(
        name: str | None = None,
        active_only: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Search Council members by name; optionally filter to currently active members."""
        return _search_people(conn, name=name, active_only=active_only, limit=limit)

    @server.tool()
    def get_person(slug: str) -> dict | None:
        """Fetch a Council member's profile by slug."""
        return _get_person(conn, archive_root, slug)

    @server.tool()
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
    def get_event(id: int) -> dict | None:
        """Fetch a single event's full record by numeric ID."""
        return _get_event(conn, archive_root, id)

    @server.tool()
    def list_committees() -> list[dict]:
        """List all committees with bill and event counts."""
        return _list_committees(conn)

    return server


async def main() -> None:
    """Boot the MCP server over stdio."""
    server = make_server()
    await server.run_stdio_async()
