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
    } <= tables
