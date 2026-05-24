"""Command-line entrypoint for the Legistar MCP server.

Two subcommands:
- `legistar-mcp index` — build or refresh the SQLite index from the archive.
- `legistar-mcp serve` — run the MCP server over stdio.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from .db import init_db
from .index.bulk import build_all


@click.group()
def main() -> None:
    """Legistar MCP server."""


@main.command()
@click.option(
    "--archive",
    "archive_root",
    required=True,
    envvar="LEGISTAR_ARCHIVE_PATH",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help=(
        "Path to a local checkout of the NYC Legistar JSON archive "
        "(reference upstream: https://github.com/jehiah/nyc_legislation). "
        "This server does NOT bundle or fetch data; you must supply the archive. "
        "Falls back to $LEGISTAR_ARCHIVE_PATH if set."
    ),
)
@click.option(
    "--db",
    "db_path",
    default=lambda: os.environ.get("LEGISTAR_DB_PATH", "data/legistar.db"),
    type=click.Path(path_type=Path),
    help="Where to write the SQLite index. Defaults to $LEGISTAR_DB_PATH or data/legistar.db.",
)
@click.option(
    "--incremental/--full",
    default=True,
    help="Incremental skips files whose LastModified is unchanged; --full rebuilds everything.",
)
def index(archive_root: Path, db_path: Path, incremental: bool) -> None:
    """Build (or refresh) the SQLite index from the NYC Legistar archive.

    Requires a local checkout of the archive. See:
    https://github.com/jehiah/nyc_legislation
    """
    conn = init_db(db_path)
    try:
        stats = build_all(
            conn,
            archive_root=archive_root,
            incremental=incremental,
            show_progress=True,
        )
    except RuntimeError as exc:
        # build_all raises RuntimeError when a stale-schema DB is asked to do
        # an incremental reindex. Surface the message cleanly rather than as a
        # Python traceback — end users running the CLI are unlikely to read
        # past the first traceback line.
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    click.echo(
        f"Indexed: bills={stats['bills']} events={stats['events']} people={stats['people']}"
    )


@main.command()
def serve() -> None:
    """Run the MCP server over stdio."""
    from .server import main as server_main

    asyncio.run(server_main())


if __name__ == "__main__":
    main()
