import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.events import get_event


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn, fixtures_root


def test_get_event_returns_raw_json(indexed_db):
    conn, root = indexed_db
    event_id = conn.execute("SELECT id FROM events LIMIT 1").fetchone()["id"]
    event = get_event(conn, archive_root=root, id=event_id)
    assert event["ID"] == event_id
    assert "Items" in event


def test_get_event_missing_returns_none(indexed_db):
    conn, root = indexed_db
    assert get_event(conn, archive_root=root, id=999999999) is None


def test_get_event_includes_legistar_url(indexed_db):
    conn, root = indexed_db
    event_id = conn.execute("SELECT id FROM events LIMIT 1").fetchone()["id"]
    event = get_event(conn, archive_root=root, id=event_id)
    assert event["LegistarURL"] == (
        "https://legistar.council.nyc.gov/MeetingDetail.aspx"
        f"?ID={event['ID']}&GUID={event['GUID']}"
    )
