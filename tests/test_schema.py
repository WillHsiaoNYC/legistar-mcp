import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).parent.parent / "src" / "legistar_mcp" / "index" / "schema.sql"

def test_schema_applies_cleanly():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}
    assert {"bills", "bills_fts", "events", "events_fts", "people", "sponsors"} <= tables
