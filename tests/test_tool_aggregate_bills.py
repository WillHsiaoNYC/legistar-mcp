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


def test_aggregate_bills_year_to_includes_dec_31(indexed_db):
    """A bill introduced 2024-12-31 (stored as a full ISO timestamp) must be
    counted by year_to=2024. Old code compared against the date-only string
    "2024-12-31" which lex-excludes the full timestamp."""
    indexed_db.execute(
        "INSERT INTO bills (id, file, intro_date, path) VALUES (?, ?, ?, ?)",
        (999001, "Int 9999-2024", "2024-12-31T23:59:59Z", "bills/synthetic.json"),
    )
    indexed_db.commit()
    rows = aggregate_bills(indexed_db, group_by=["intro_year"], year_to=2024)
    by_year = {r["intro_year"]: r["count"] for r in rows}
    assert 2024 in by_year
    # Total includes our synthetic Dec 31 bill + at least the existing 2024 fixture.
    assert by_year[2024] >= 2
