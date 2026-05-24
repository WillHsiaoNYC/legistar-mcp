import json
import sys
from contextlib import nullcontext
from pathlib import Path
from sqlite3 import Connection

import click

from ..db import SCHEMA_VERSION
from .build import index_bill_file, index_event_file, index_person_file


# Real archive groups bills by Legistar type. Three of these directories
# contain actual bill records (introduction/, land_use/, resolution/);
# resubmit/<year>.json was originally listed as a fourth type in the plan
# but on inspection of the real archive each file there has shape
# {"Resubmitted": [{"FromFile": ..., "ToFile": ...}]} (resubmission
# mapping, not bill records), so it is excluded from the bill walk.
_BILL_TYPE_DIRS = ("introduction", "land_use", "resolution")


def _bill_paths(root: Path):
    found_any = False
    for d in _BILL_TYPE_DIRS:
        if (root / d).exists():
            found_any = True
            yield from sorted((root / d).rglob("*.json"))
    if not found_any and (root / "bills").exists():
        yield from sorted((root / "bills").glob("*.json"))


def _event_paths(root: Path):
    if (root / "events").exists():
        yield from sorted((root / "events").rglob("*.json"))


def _person_paths(root: Path):
    if (root / "people").exists():
        yield from sorted((root / "people").glob("*.json"))


def _last_modified_of(path: Path) -> str | None:
    with open(path, encoding="utf-8") as f:
        return (json.load(f) or {}).get("LastModified")


def build_all(
    conn: Connection,
    archive_root: Path,
    incremental: bool = False,
    show_progress: bool = False,
) -> dict[str, int]:
    # Refuse to run incremental when the DB was indexed under an older schema
    # version. Incremental only re-walks files whose LastModified changed, so
    # tables/columns introduced by a newer release would stay empty/NULL for
    # all the files that didn't change since the last index. The user gets a
    # silently-partial DB and no warning. Force --full so new tables get
    # backfilled across the whole archive. Check happens before any expensive
    # filesystem walk.
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if incremental and current_version < SCHEMA_VERSION:
        raise RuntimeError(
            f"Incremental reindex refused: schema version is {SCHEMA_VERSION} "
            f"but indexed data is at version {current_version}. New tables added "
            f"in this upgrade need to be backfilled across the whole archive. "
            f"Re-run with --full to fix."
        )

    # Persist archive_root so query-time tools can resolve relative bills.path
    # back to the source JSON (needed for building snippets server-side, since
    # bills_fts is contentless and SQLite's snippet() returns NULL on it).
    conn.execute(
        "INSERT OR REPLACE INTO index_state (key, value) VALUES ('archive_root', ?)",
        (str(archive_root.resolve()),),
    )

    # Materialize the path generators so the progress bars know totals upfront.
    bills = list(_bill_paths(archive_root))
    events = list(_event_paths(archive_root))
    people = list(_person_paths(archive_root))

    seen_bills: dict[str, str | None] = {}
    seen_events: dict[str, str | None] = {}
    if incremental:
        seen_bills = dict(conn.execute("SELECT path, last_modified FROM bills").fetchall())
        seen_events = dict(conn.execute("SELECT path, last_modified FROM events").fetchall())

    stats = {"bills": 0, "events": 0, "people": 0}
    archive_resolved = archive_root.resolve()

    def _bar(items: list[Path], label: str):
        if show_progress:
            return click.progressbar(items, label=label, file=sys.stderr)
        return nullcontext(items)

    with _bar(bills, "Bills ") as it:
        for p in it:
            if incremental:
                rel = p.resolve().relative_to(archive_resolved).as_posix()
                if seen_bills.get(rel) == _last_modified_of(p):
                    continue
            index_bill_file(conn, p, archive_root)
            stats["bills"] += 1

    with _bar(events, "Events") as it:
        for p in it:
            if incremental:
                rel = p.resolve().relative_to(archive_resolved).as_posix()
                if seen_events.get(rel) == _last_modified_of(p):
                    continue
            index_event_file(conn, p, archive_root)
            stats["events"] += 1

    # People are small; always re-index.
    with _bar(people, "People") as it:
        for p in it:
            index_person_file(conn, p, archive_root)
            stats["people"] += 1

    # Only a full rebuild guarantees every row matches the current schema
    # (incremental skips unchanged files, so new columns/tables may stay NULL
    # or empty). Bump user_version only when the data is known consistent.
    if not incremental:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    conn.commit()
    return stats
