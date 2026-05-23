import json
from pathlib import Path
from sqlite3 import Connection
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
    conn: Connection, archive_root: Path, incremental: bool = False
) -> dict[str, int]:
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

    for p in _bill_paths(archive_root):
        if incremental:
            rel = p.resolve().relative_to(archive_root.resolve()).as_posix()
            if seen_bills.get(rel) == _last_modified_of(p):
                continue
        index_bill_file(conn, p, archive_root)
        stats["bills"] += 1

    for p in _event_paths(archive_root):
        if incremental:
            rel = p.resolve().relative_to(archive_root.resolve()).as_posix()
            if seen_events.get(rel) == _last_modified_of(p):
                continue
        index_event_file(conn, p, archive_root)
        stats["events"] += 1

    # People are small; always re-index (per plan).
    for p in _person_paths(archive_root):
        index_person_file(conn, p, archive_root)
        stats["people"] += 1

    conn.commit()
    return stats
