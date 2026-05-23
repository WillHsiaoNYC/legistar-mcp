"""Command-line entrypoint for the Legistar MCP server.

Two subcommands:
- `legistar-mcp index` — build or refresh the SQLite index from the archive.
- `legistar-mcp serve` — run the MCP server over stdio.
"""

from __future__ import annotations

import asyncio
import os
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
)
@click.option(
    "--db",
    "db_path",
    default=lambda: os.environ.get("LEGISTAR_DB_PATH", "data/legistar.db"),
    type=click.Path(path_type=Path),
)
@click.option("--incremental/--full", default=True)
def index(archive_root: Path, db_path: Path, incremental: bool) -> None:
    """Build (or refresh) the SQLite index from the NYC Legistar archive."""
    conn = init_db(db_path)
    stats = build_all(conn, archive_root=archive_root, incremental=incremental)
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
