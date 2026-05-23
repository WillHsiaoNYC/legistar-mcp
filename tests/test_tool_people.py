import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.people import search_people, get_person


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn, fixtures_root


def test_search_people_returns_rows(indexed_db):
    conn, _ = indexed_db
    results = search_people(conn, limit=5)
    assert len(results) >= 1
    assert "slug" in results[0]


def test_get_person_returns_raw_json(indexed_db):
    conn, root = indexed_db
    slug = conn.execute("SELECT slug FROM people LIMIT 1").fetchone()["slug"]
    person = get_person(conn, archive_root=root, slug=slug)
    assert person["Slug"] == slug
    assert "_stats" in person
    assert "sponsored_bill_count_by_status" in person["_stats"]
