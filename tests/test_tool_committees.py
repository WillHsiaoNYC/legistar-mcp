import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.committees import list_committees


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_list_committees_returns_aggregated_counts(indexed_db):
    results = list_committees(indexed_db)
    assert isinstance(results, list)
    assert len(results) >= 1
    row = results[0]
    assert "name" in row
    assert "bill_count" in row
    assert "event_count" in row
    assert row["bill_count"] >= 0
    assert row["event_count"] >= 0


def test_list_committees_orders_by_total_desc(indexed_db):
    results = list_committees(indexed_db)
    totals = [r["bill_count"] + r["event_count"] for r in results]
    assert totals == sorted(totals, reverse=True)
