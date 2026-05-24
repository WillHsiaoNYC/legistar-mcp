import sqlite3
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "index" / "schema.sql"

# Bumped whenever a code release ships a schema/data change that requires a
# `legistar-mcp index --full` re-run to populate. See _warn_if_stale().
#
# Version history:
#   1 — `guid` columns added to bills and events; needs --full to backfill
#       (existing rows have NULL guid until rebuilt).
#   2 — `event_items` table added for bill↔event linkage (Batch B). NULL for
#       existing DBs until --full re-runs the event indexer.
SCHEMA_VERSION = 2


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    _warn_if_stale(conn)
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive migrations to a pre-existing DB.

    CREATE TABLE IF NOT EXISTS is a no-op on existing tables, so columns added
    after a DB was first created need explicit ALTER TABLE. Each step probes
    via PRAGMA table_info and is idempotent. Safe to call on a brand-new DB:
    when the table doesn't exist yet, init_db's executescript will create it
    with the current schema and the migration step short-circuits.
    """
    for table in ("bills", "events"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        if "guid" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN guid TEXT")
    conn.commit()

    # event_items is new in SCHEMA_VERSION 2 (Batch B, tools expansion).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_items (
            item_id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id),
            bill_id INTEGER NOT NULL,
            item_title TEXT,
            item_sequence INTEGER,
            action_name TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_items_bill ON event_items(bill_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_items_event ON event_items(event_id)")
    conn.commit()


def _warn_if_stale(conn: sqlite3.Connection) -> None:
    """Warn the user (via stderr) if their indexed data is older than the schema
    version this code expects.

    Triggers when (a) the bills table exists and has rows, and (b) the stored
    PRAGMA user_version is below SCHEMA_VERSION. The version is bumped by
    build_all() after a successful --full reindex, so the warning auto-clears
    once the user runs `legistar-mcp index --full`.

    Quiet on empty / brand-new / current-version DBs.
    """
    has_bills = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bills'"
    ).fetchone()
    if not has_bills:
        return
    row_count = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
    if row_count == 0:
        return
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    sys.stderr.write(
        f"⚠ legistar-mcp: indexed data is at schema version {current}, "
        f"code expects {SCHEMA_VERSION}. Run `legistar-mcp index --full` "
        f"to backfill new columns/tables, then this warning will clear.\n"
    )
