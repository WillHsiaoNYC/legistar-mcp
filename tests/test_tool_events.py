import pytest
from freezegun import freeze_time

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.events import search_events, upcoming_events


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


def test_search_events_date_filter_rejects_out_of_range(indexed_db):
    # The fixture event is from 2024; a 2099 floor must exclude it.
    # This exercises the rejection path the previous test couldn't (only
    # one fixture row meant the >= constraint passed trivially).
    results = search_events(indexed_db, date_from="2099-01-01", limit=5)
    assert results == []


def test_search_events_results_include_legistar_url(indexed_db):
    results = search_events(indexed_db, limit=5)
    assert results
    hit = results[0]
    assert hit["legistar_url"] == (
        "https://legistar.council.nyc.gov/MeetingDetail.aspx"
        f"?ID={hit['id']}&GUID=2E5AFBF9-B5AA-4295-B8F2-3368AB913D57"
    )
    # The transient guid column from the SELECT must not leak into output.
    assert "guid" not in hit


@freeze_time("2024-08-01")
def test_upcoming_events_within_window(indexed_db):
    # Fixture event is 2024-08-15. Frozen 2024-08-01 + days=30 catches it.
    results = upcoming_events(indexed_db, days=30, limit=10)
    assert results
    assert all(r["date"] >= "2024-08-01" for r in results)


@freeze_time("2024-08-20")
def test_upcoming_events_empty_when_no_future(indexed_db):
    # Past the fixture event's date — empty.
    assert upcoming_events(indexed_db, days=14) == []


@freeze_time("2024-08-01")
def test_upcoming_events_includes_boundary_day(indexed_db):
    # Fixture event is 2024-08-15T13:30:00-04:00. Frozen 2024-08-01 + days=14
    # makes the cutoff day exactly 2024-08-15. The function must include
    # events whose date STARTS with that day even though the stored value is
    # a full ISO timestamp lex-greater than "2024-08-15".
    results = upcoming_events(indexed_db, days=14, limit=10)
    assert any(r["date"].startswith("2024-08-15") for r in results)


@freeze_time("2024-08-01")
def test_upcoming_events_have_legistar_url(indexed_db):
    results = upcoming_events(indexed_db, days=30)
    assert results and "legistar_url" in results[0]


def test_get_bill_hearings_returns_event_for_known_bill(indexed_db):
    """The fixture event has 3 bill-bearing items; querying by Int 0153-2022 returns the event."""
    from legistar_mcp.tools.events import get_bill_hearings
    results = get_bill_hearings(indexed_db, file="Int 0153-2022")
    assert any(r["id"] == 21015 for r in results)
    hit = next(r for r in results if r["id"] == 21015)
    assert hit["action_name"] == "Hearing Held by Committee"
    assert hit["item_title"].startswith("Int 0153-2022")
    assert "legistar_url" in hit


def test_get_bill_hearings_unknown_file_returns_empty(indexed_db):
    from legistar_mcp.tools.events import get_bill_hearings
    assert get_bill_hearings(indexed_db, file="Int 9999-9999") == []


def test_get_bill_hearings_requires_file_or_id(indexed_db):
    from legistar_mcp.tools.events import get_bill_hearings
    with pytest.raises(ValueError):
        get_bill_hearings(indexed_db)


def test_get_bill_hearings_raises_stale_index_when_event_items_empty(indexed_db):
    from legistar_mcp.tools.events import get_bill_hearings
    from legistar_mcp._db_utils import StaleIndexError
    indexed_db.execute("DELETE FROM event_items")
    with pytest.raises(StaleIndexError, match="--full"):
        get_bill_hearings(indexed_db, file="Int 0153-2022")


@freeze_time("2024-08-01")
def test_get_bill_hearings_only_upcoming_sorts_earliest_first(indexed_db):
    """When only_upcoming=True, the nearest-future hearing must come first.

    Insert three synthetic future events + event_items for an existing bill
    and verify the result order is ASC by date (next hearing first).
    """
    from legistar_mcp.tools.events import get_bill_hearings

    # Synthetic future events for bill 68628 (Int 0153-2022).
    rows = [
        (90001, "GUID-1", 1, "Committee A", "2024-09-10T10:00:00-04:00", "Loc", "events/x1.json"),
        (90002, "GUID-2", 1, "Committee A", "2024-08-15T10:00:00-04:00", "Loc", "events/x2.json"),
        (90003, "GUID-3", 1, "Committee A", "2024-10-05T10:00:00-04:00", "Loc", "events/x3.json"),
    ]
    for r in rows:
        indexed_db.execute(
            "INSERT INTO events (id, guid, body_id, body_name, date, location, path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            r,
        )
    items = [
        (900011, 90001, 68628, "title", 1, "Action"),
        (900021, 90002, 68628, "title", 1, "Action"),
        (900031, 90003, 68628, "title", 1, "Action"),
    ]
    for it in items:
        indexed_db.execute(
            "INSERT INTO event_items "
            "(item_id, event_id, bill_id, item_title, item_sequence, action_name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            it,
        )
    indexed_db.commit()

    results = get_bill_hearings(indexed_db, id=68628, only_upcoming=True)
    future_ids = [r["id"] for r in results if r["id"] in {90001, 90002, 90003}]
    assert future_ids == [90002, 90001, 90003]  # ASC by date


def test_get_event_bills_returns_bills_for_known_event(indexed_db):
    from legistar_mcp.tools.events import get_event_bills
    results = get_event_bills(indexed_db, event_id=21015)
    # Fixture event has 3 bill-bearing items
    assert len(results) == 3
    files = {r["file"] for r in results}
    assert "Int 0153-2022" in files
    assert "Int 0001-2024" in files
    assert "Int 0938-2023" in files
    assert all("legistar_url" in r for r in results)


def test_get_event_bills_unknown_event_returns_empty(indexed_db):
    from legistar_mcp.tools.events import get_event_bills
    assert get_event_bills(indexed_db, event_id=99999999) == []


def test_get_event_bills_sorted_by_sequence_asc(indexed_db):
    from legistar_mcp.tools.events import get_event_bills
    results = get_event_bills(indexed_db, event_id=21015)
    sequences = [r["item_sequence"] for r in results]
    assert sequences == sorted(sequences)
