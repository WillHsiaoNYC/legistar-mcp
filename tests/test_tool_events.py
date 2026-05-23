import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.events import search_events


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_search_events_returns_rows(indexed_db):
    results = search_events(indexed_db, limit=5)
    assert len(results) >= 1
    assert "date" in results[0]
    assert "body_name" in results[0]


def test_search_events_filters_by_date_range(indexed_db):
    results = search_events(indexed_db, date_from="2024-01-01", limit=5)
    assert all(r["date"] >= "2024-01-01" for r in results)
