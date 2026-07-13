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
