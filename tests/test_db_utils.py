import pytest
from legistar_mcp._db_utils import StaleIndexError, _check_table_populated
from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all


def test_raises_stale_index_error_when_user_version_below_schema_version(
    tmp_path, fixtures_root
):
    """A populated DB whose user_version is stuck below SCHEMA_VERSION (the
    classic "upgraded package, ran incremental, never --full" scenario) must
    raise so tools fail loudly instead of returning partial results."""
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    # build_all bumps user_version to SCHEMA_VERSION. Force it back to simulate stale.
    conn.execute("PRAGMA user_version = 1")
    with pytest.raises(StaleIndexError, match="--full"):
        _check_table_populated(conn, "event_items", "events")


def test_silent_when_user_version_matches(tmp_path, fixtures_root):
    """After --full, user_version == SCHEMA_VERSION → silent."""
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    _check_table_populated(conn, "event_items", "events")


def test_silent_when_no_data_at_all(tmp_path):
    """Brand-new DB; user_version is 0 < SCHEMA_VERSION but related table is
    empty → silent (don't bother freshly-initialized DBs)."""
    conn = init_db(tmp_path / "t.db")
    _check_table_populated(conn, "event_items", "events")


def test_silent_when_target_empty_but_db_is_current(tmp_path, fixtures_root):
    """Legitimately empty target table after a full reindex (e.g., archive
    with no votes-bearing bills) should NOT raise — user_version matches
    SCHEMA_VERSION. This is the false-positive case the rewrite fixes:
    previously, _check_table_populated would mistake "target empty + related
    has rows" for staleness even when the DB was fully current."""
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    # Wipe event_items even though we're current. Should not raise.
    conn.execute("DELETE FROM event_items")
    _check_table_populated(conn, "event_items", "events")
