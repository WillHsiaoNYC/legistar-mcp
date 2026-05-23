import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "index" / "schema.sql"


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
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
    skips if the table doesn't exist yet (init_db's executescript will create
    it with the current schema).
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(bills)")}
    if not cols:
        return
    if "guid" not in cols:
        conn.execute("ALTER TABLE bills ADD COLUMN guid TEXT")
        conn.commit()
