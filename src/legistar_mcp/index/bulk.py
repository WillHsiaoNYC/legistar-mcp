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


def _purge_missing(
    conn: Connection,
    archive_resolved: Path,
    *,
    bills: list[Path],
    events: list[Path],
    people: list[Path],
) -> int:
    """Delete rows (and their dependents) whose `path` was not walked this run.
    Runs on every build; on an unchanged archive the NOT-IN sets are empty and
    this is a cheap no-op."""

    def rels(paths: list[Path]) -> set[str]:
        return {p.resolve().relative_to(archive_resolved).as_posix() for p in paths}

    removed = 0
    with conn:
        walked = rels(bills)
        gone = [
            r["id"]
            for r in conn.execute("SELECT id, path FROM bills")
            if r["path"] not in walked
        ]
        for bid in gone:
            fts = conn.execute(
                "SELECT fts_rowid FROM bills_fts_map WHERE bill_id = ?", (bid,)
            ).fetchone()
            if fts:
                conn.execute("DELETE FROM bills_fts WHERE rowid = ?", (fts["fts_rowid"],))
            conn.execute("DELETE FROM bills_fts_map WHERE bill_id = ?", (bid,))
            conn.execute("DELETE FROM sponsors WHERE bill_id = ?", (bid,))
            conn.execute("DELETE FROM votes WHERE bill_id = ?", (bid,))
            conn.execute("DELETE FROM bills WHERE id = ?", (bid,))
        removed += len(gone)

        walked = rels(events)
        gone = [
            r["id"]
            for r in conn.execute("SELECT id, path FROM events")
            if r["path"] not in walked
        ]
        for eid in gone:
            for m in conn.execute(
                "SELECT fts_rowid FROM events_fts_map WHERE event_id = ?", (eid,)
            ).fetchall():
                conn.execute("DELETE FROM events_fts WHERE rowid = ?", (m["fts_rowid"],))
            conn.execute("DELETE FROM events_fts_map WHERE event_id = ?", (eid,))
            conn.execute("DELETE FROM event_items WHERE event_id = ?", (eid,))
            conn.execute("DELETE FROM events WHERE id = ?", (eid,))
        removed += len(gone)

        walked = rels(people)
        gone = [
            r["slug"]
            for r in conn.execute("SELECT slug, path FROM people")
            if r["path"] not in walked
        ]
        for slug in gone:
            conn.execute("DELETE FROM people WHERE slug = ?", (slug,))
        removed += len(gone)
    return removed


def build_all(
    conn: Connection,
    archive_root: Path,
    incremental: bool = False,
    show_progress: bool = False,
) -> dict[str, int]:
    # A brand-new DB (no bills rows) has user_version=0, which the stale-schema
    # guard below would refuse even though there is nothing stale — the CLI
    # default (--incremental) would then fail on first run. Incremental is
    # meaningless with no prior rows anyway, so promote to a full build; that
    # also stamps user_version at the end.
    has_rows = conn.execute("SELECT 1 FROM bills LIMIT 1").fetchone() is not None
    if incremental and not has_rows:
        incremental = False

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

    # Materialize the path generators so the progress bars know totals upfront.
    bills = list(_bill_paths(archive_root))
    events = list(_event_paths(archive_root))
    people = list(_person_paths(archive_root))

    # A wrong --archive path (e.g. the parent directory of the real clone)
    # walks zero files and would otherwise "succeed" with bills=0, persist the
    # junk path, and leave every tool returning [] with no diagnostic.
    if not bills and not events and not people:
        raise RuntimeError(
            f"No archive content found under {archive_root}. Expected "
            f"subdirectories like introduction/, resolution/, land_use/, "
            f"events/, people/ (see jehiah/nyc_legislation). Check the "
            f"--archive path."
        )

    # Persist archive_root so query-time tools can resolve relative bills.path
    # back to the source JSON (needed for building snippets server-side, since
    # bills_fts is contentless and SQLite's snippet() returns NULL on it).
    conn.execute(
        "INSERT OR REPLACE INTO index_state (key, value) VALUES ('archive_root', ?)",
        (str(archive_root.resolve()),),
    )

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
                lm = _last_modified_of(p)
                # `lm is None` must never match: a file with no LastModified
                # would compare None == None against a never-seen path and be
                # skipped forever. Unknown paths and None stamps always index.
                if rel in seen_bills and lm is not None and seen_bills[rel] == lm:
                    continue
            index_bill_file(conn, p, archive_root)
            stats["bills"] += 1

    with _bar(events, "Events") as it:
        for p in it:
            if incremental:
                rel = p.resolve().relative_to(archive_resolved).as_posix()
                lm = _last_modified_of(p)
                # `lm is None` must never match: a file with no LastModified
                # would compare None == None against a never-seen path and be
                # skipped forever. Unknown paths and None stamps always index.
                if rel in seen_events and lm is not None and seen_events[rel] == lm:
                    continue
            index_event_file(conn, p, archive_root)
            stats["events"] += 1

    # People are small; always re-index.
    with _bar(people, "People") as it:
        for p in it:
            index_person_file(conn, p, archive_root)
            stats["people"] += 1

    # Purge rows whose source file vanished from the archive (upstream delete
    # or rename). Without this, phantom records survive every reindex — even
    # --full only ever INSERT-OR-REPLACEs what the walk finds.
    stats["removed"] = _purge_missing(
        conn, archive_resolved, bills=bills, events=events, people=people
    )

    # Only a full rebuild guarantees every row matches the current schema
    # (incremental skips unchanged files, so new columns/tables may stay NULL
    # or empty). Bump user_version only when the data is known consistent.
    if not incremental:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    conn.commit()
    return stats
