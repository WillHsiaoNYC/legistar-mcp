import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.events import aggregate_events


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_aggregate_events_groups_by_body_name(indexed_db):
    rows = aggregate_events(indexed_db, group_by=["body_name"])
    assert isinstance(rows, list)
    assert rows
    assert all("body_name" in r and "count" in r for r in rows)
    assert sum(r["count"] for r in rows) >= 1


def test_aggregate_events_event_year_returns_integer(indexed_db):
    rows = aggregate_events(indexed_db, group_by=["event_year"])
    assert rows
    assert all(isinstance(r["event_year"], int) for r in rows)


def test_aggregate_events_multi_dim_group_by(indexed_db):
    rows = aggregate_events(indexed_db, group_by=["body_name", "event_year"])
    assert rows
    assert all(
        "body_name" in r and "event_year" in r and "count" in r for r in rows
    )


def test_aggregate_events_rejects_unknown_group_by(indexed_db):
    with pytest.raises(ValueError):
        aggregate_events(indexed_db, group_by=["nonexistent"])


def test_aggregate_events_rejects_empty_group_by(indexed_db):
    with pytest.raises(ValueError):
        aggregate_events(indexed_db, group_by=[])


def test_aggregate_events_date_range_filters_results(indexed_db):
    # Fixture's only event is 2024-08-15. A 2099 floor must exclude it.
    rows = aggregate_events(
        indexed_db, group_by=["body_name"], date_from="2099-01-01"
    )
    assert rows == []


def test_aggregate_events_committee_filter_narrows(indexed_db):
    # The fixture's body_name is "City Council"; filtering to a bogus name
    # must produce no rows even though the unfiltered call has data.
    unfiltered = aggregate_events(indexed_db, group_by=["body_name"])
    assert unfiltered
    rows = aggregate_events(
        indexed_db, group_by=["body_name"], committee="Nonexistent Committee"
    )
    assert rows == []
