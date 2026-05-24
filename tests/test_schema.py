import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "src" / "legistar_mcp" / "index" / "schema.sql"

def test_schema_applies_cleanly():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    # Include FTS5 shadow tables so the assertion proves FTS5 actually ran
    # (a regular table named "bills_fts" would satisfy a weaker subset check).
    assert {
        "bills", "bills_fts", "bills_fts_data",
        "events", "events_fts", "events_fts_data",
        "people", "sponsors",
        "bills_fts_map", "events_fts_map", "index_state",
        "event_items", "votes",
    } <= tables


def test_schema_creates_event_items_and_votes_indexes():
    """The new SCHEMA_VERSION 2/3 tables ship with indexes that the query
    planner relies on (e.g., vote_breakdown joins on bill_id, get_voting_record
    filters by person_slug + vote_date). A typo in any CREATE INDEX statement
    would silently degrade query performance — this test asserts the exact
    set so renames/removals fail loudly."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    expected = {
        # event_items (SCHEMA_VERSION 2, Batch B)
        "idx_event_items_bill",
        "idx_event_items_event",
        # votes (SCHEMA_VERSION 3, Batch C)
        "idx_votes_bill",
        "idx_votes_event",
        "idx_votes_person",
        "idx_votes_person_date",
    }
    missing = expected - indexes
    assert not missing, f"missing indexes: {missing}"
