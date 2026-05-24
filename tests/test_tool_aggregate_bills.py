import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import aggregate_bills


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_aggregate_bills_groups_by_status(indexed_db):
    rows = aggregate_bills(indexed_db, group_by=["status_name"])
    assert isinstance(rows, list)
    assert all("count" in r for r in rows)
    assert all("status_name" in r for r in rows)
    assert sum(r["count"] for r in rows) >= 2


def test_aggregate_bills_multi_dim_group_by(indexed_db):
    rows = aggregate_bills(indexed_db, group_by=["status_name", "type_name"])
    assert all("status_name" in r and "type_name" in r and "count" in r for r in rows)


def test_aggregate_bills_intro_year_returns_integer(indexed_db):
    rows = aggregate_bills(indexed_db, group_by=["intro_year"])
    assert rows
    assert all(isinstance(r["intro_year"], int) for r in rows)


def test_aggregate_bills_rejects_unknown_group_by(indexed_db):
    with pytest.raises(ValueError):
        aggregate_bills(indexed_db, group_by=["nonexistent"])
