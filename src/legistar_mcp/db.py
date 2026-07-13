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
#   3 — `votes` table added for council voting records (Batch C). Empty for
#       existing DBs until --full re-runs the bill indexer.
#   4 — `events.insite_url` column added. The prior practice of constructing
#       MeetingDetail.aspx URLs from API ID/GUID emitted "Invalid parameters!"
#       links; the source JSON's InSiteURL is authoritative. NULL on existing
#       rows until --full re-runs the event indexer.
#   5 — event_items.item_sequence falsy-zero fix + stale-row purge +
#       index_state.last_indexed. --full backfills corrected sequences and
#       clears any phantom rows from pre-purge releases.
SCHEMA_VERSION = 5


def open_db(db_path: Path) -> sqlite3.Connection:
    # schema.sql's FTS5 tables use contentless_delete=1, added in SQLite 3.43
    # (Aug 2023). Older libraries fail CREATE VIRTUAL TABLE with an opaque
    # "unrecognized option" error, and the indexer's DELETE FROM *_fts is
    # illegal on contentless tables without it. (3.43 also covers the NULLS
    # LAST ordering vote_breakdown needs, added in 3.30.) Modern CPython and
    # uv-managed interpreters bundle 3.45+; Linux system Pythons may not.
    if sqlite3.sqlite_version_info < (3, 43):
        raise RuntimeError(
            f"SQLite >= 3.43 required (FTS5 contentless_delete). Found "
            f"{sqlite3.sqlite_version}. Use a Python whose sqlite3 links a "
            f"newer libsqlite3 (e.g. a uv-managed interpreter: uv python install)."
        )
    # check_same_thread=False lets a single Connection be used from any thread
    # — required because FastMCP can dispatch tools off the main thread. The
    # server.py module guards every tool call with a module-level lock so the
    # connection is still accessed serially (sqlite3 itself is not safe under
    # concurrent use of one Connection).
    conn = sqlite3.connect(db_path, check_same_thread=False)
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

    # insite_url added in SCHEMA_VERSION 4 — authoritative event web URL.
    events_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if events_cols and "insite_url" not in events_cols:
        conn.execute("ALTER TABLE events ADD COLUMN insite_url TEXT")
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

    # votes is new in SCHEMA_VERSION 3 (Batch C, tools expansion).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            history_record_id INTEGER NOT NULL,
            person_slug TEXT NOT NULL,
            bill_id INTEGER NOT NULL,
            event_id INTEGER,
            vote_value TEXT NOT NULL,
            vote_date TEXT,
            action TEXT,
            passed_flag INTEGER,
            PRIMARY KEY (history_record_id, person_slug)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_bill ON votes(bill_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_person ON votes(person_slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_person_date ON votes(person_slug, vote_date)")
    # idx_votes_event was added in v0.2.0 but no shipped query filters by
    # event_id. Drop it on upgrade to reclaim write throughput on indexing.
    # New DBs never get it (removed from schema.sql); existing v0.2.0 DBs
    # have it cleaned out here. Safe no-op when the index isn't present.
    conn.execute("DROP INDEX IF EXISTS idx_votes_event")
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
