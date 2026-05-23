import sqlite3
from legistar_mcp.db import open_db, init_db

def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "bills" in tables
    assert "events" in tables
    conn.close()

def test_open_db_enables_foreign_keys(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = open_db(db_path)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_open_db_backfills_guid_column_on_legacy_db(tmp_path):
    """Simulate an existing DB created before the guid column was added: server
    boots via open_db() and the migration must add the column to both bills
    and events, otherwise queries will fail with 'no such column: ...guid'.
    """
    db_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE bills (id INTEGER PRIMARY KEY, file TEXT UNIQUE NOT NULL)"
    )
    legacy.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, path TEXT NOT NULL)"
    )
    legacy.commit()
    legacy.close()

    conn = open_db(db_path)
    bill_cols = {r["name"] for r in conn.execute("PRAGMA table_info(bills)")}
    event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "guid" in bill_cols
    assert "guid" in event_cols
