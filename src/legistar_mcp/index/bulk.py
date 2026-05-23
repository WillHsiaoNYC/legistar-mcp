from pathlib import Path
from sqlite3 import Connection
from .build import index_bill_file, index_event_file, index_person_file


def _bill_paths(root: Path):
    if (root / "introduction").exists():
        yield from sorted((root / "introduction").rglob("*.json"))
    elif (root / "bills").exists():
        yield from sorted((root / "bills").glob("*.json"))


def _event_paths(root: Path):
    if (root / "events").exists():
        yield from sorted((root / "events").rglob("*.json"))


def _person_paths(root: Path):
    if (root / "people").exists():
        yield from sorted((root / "people").glob("*.json"))


def build_all(conn: Connection, archive_root: Path) -> dict[str, int]:
    stats = {"bills": 0, "events": 0, "people": 0}
    for p in _bill_paths(archive_root):
        index_bill_file(conn, p, archive_root)
        stats["bills"] += 1
    for p in _event_paths(archive_root):
        index_event_file(conn, p, archive_root)
        stats["events"] += 1
    for p in _person_paths(archive_root):
        index_person_file(conn, p, archive_root)
        stats["people"] += 1
    conn.commit()
    return stats
