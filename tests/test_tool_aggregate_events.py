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


def test_aggregate_events_date_to_includes_boundary_day(indexed_db):
    # The only fixture event is 2024-08-15T13:30:00-04:00. Asking for events
    # through 2024-08-15 (a bare date) must count it — the tool's headline use
    # case ("hearings in <year>" -> date_to='<year>-12-31') depends on this.
    rows = aggregate_events(
        indexed_db, group_by=["body_name"], date_to="2024-08-15"
    )
    assert rows
    assert sum(r["count"] for r in rows) >= 1


def test_aggregate_events_event_year_excludes_null_date(indexed_db):
    # An event with a NULL date can't be bucketed into a year; it must not
    # surface as a spurious {'event_year': None} group, and every returned
    # event_year must be an int.
    indexed_db.execute(
        "INSERT INTO events (id, body_name, date, path) VALUES (?, ?, NULL, ?)",
        (970001, "City Council", "events/nulldate.json"),
    )
    indexed_db.commit()
    rows = aggregate_events(indexed_db, group_by=["event_year"])
    assert rows
    assert all(r["event_year"] is not None for r in rows)
    assert all(isinstance(r["event_year"], int) for r in rows)


def test_aggregate_events_event_month_excludes_null_date(indexed_db):
    # Same guard for the month dimension.
    indexed_db.execute(
        "INSERT INTO events (id, body_name, date, path) VALUES (?, ?, NULL, ?)",
        (970002, "City Council", "events/nulldate2.json"),
    )
    indexed_db.commit()
    rows = aggregate_events(indexed_db, group_by=["event_month"])
    assert rows
    assert all(r["event_month"] is not None for r in rows)
