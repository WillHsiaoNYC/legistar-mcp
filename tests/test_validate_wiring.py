"""Wiring tests: validation helpers must actually be applied inside each tool.
Uses the shared fixtures archive (3 bills, 1 event, 1 person)."""
import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import recent_bills, search_bills
from legistar_mcp.tools.events import search_events, upcoming_events
from legistar_mcp.tools.people import search_people
from legistar_mcp.tools.relationships import vote_breakdown


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_negative_limit_never_returns_everything(indexed_db):
    # Fixture has 3 bills; a passed-through LIMIT -1 would return all 3.
    assert len(search_bills(indexed_db, limit=-1)) == 1
    assert len(search_people(indexed_db, limit=-1)) == 1
    assert len(search_events(indexed_db, limit=-1)) == 1
    assert len(recent_bills(indexed_db, days=36500, limit=-1)) == 1
    assert len(upcoming_events(indexed_db, days=36500, limit=-1)) <= 1
    assert len(vote_breakdown(indexed_db, bill_id=68628, limit=-1)) <= 1


def test_punctuated_query_does_not_crash(indexed_db):
    # These previously raised sqlite3.OperationalError from FTS5.
    for q in ("covid-19", "don't", "safety (vision zero)", "   "):
        search_bills(indexed_db, query=q, limit=5)
        search_events(indexed_db, query=q, limit=5)


def test_query_and_agency_combine_instead_of_override(indexed_db):
    # Fixture Int 0153-2022 mentions the Mayor's Office of Operations.
    # agency alone matches it; adding an unrelated query must NARROW, not override.
    agency_only = search_bills(indexed_db, agency="Mayor's Office of Operations", limit=5)
    assert any("0153-2022" in r["file"] for r in agency_only)
    combined = search_bills(
        indexed_db, query="zzzunfindable", agency="Mayor's Office of Operations", limit=5
    )
    assert combined == []  # query was previously discarded → would return the agency hits
