import pytest
from legistar_mcp._db_utils import StaleIndexError, _check_table_populated
from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all


def test_raises_stale_index_error_when_new_table_empty(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    # Simulate "package upgraded; --full not run yet" by emptying event_items.
    conn.execute("DELETE FROM event_items")
    with pytest.raises(StaleIndexError, match="--full"):
        _check_table_populated(conn, "event_items", "events")


def test_silent_when_table_populated(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    # After the B3 fixture upgrade, event_items has 3 rows from the indexer
    # already. _check_table_populated must return silently.
    _check_table_populated(conn, "event_items", "events")


def test_silent_when_no_data_at_all(tmp_path):
    conn = init_db(tmp_path / "t.db")
    # Both tables empty — silent (not our concern).
    _check_table_populated(conn, "event_items", "events")
