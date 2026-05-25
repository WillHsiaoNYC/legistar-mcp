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


def test_list_committees_exposes_first_seen_dates(indexed_db):
    # first_bill_date / first_event_date are the earliest activity dates we
    # have in the archive for each committee — a proxy for "when did this
    # committee start showing up?". Both keys must be present on every row,
    # populated as ISO date strings where there's activity and NULL otherwise.
    results = list_committees(indexed_db)
    assert all("first_bill_date" in r and "first_event_date" in r for r in results)
    # Each date, when present, must match its count: a committee with bills
    # has a first_bill_date; one with events has a first_event_date.
    for r in results:
        if r["bill_count"] > 0:
            assert r["first_bill_date"] and r["first_bill_date"].startswith("20")
        else:
            assert r["first_bill_date"] is None
        if r["event_count"] > 0:
            assert r["first_event_date"] and r["first_event_date"].startswith("20")
        else:
            assert r["first_event_date"] is None
    # The fixture exercises both paths.
    assert any(r["first_bill_date"] for r in results)
    assert any(r["first_event_date"] for r in results)
