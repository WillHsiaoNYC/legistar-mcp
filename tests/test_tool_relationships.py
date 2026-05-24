import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.relationships import co_sponsors


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_co_sponsors_returns_overlap_above_min(indexed_db):
    results = co_sponsors(indexed_db, slug="adrienne-e-adams", min_overlap=2, limit=20)
    # The MOO + new sponsor-overlap fixture share >=N co-sponsors with Adams.
    # Document expected N in Task A6 commit message and assert it here.
    assert any(r["overlap_count"] >= 2 for r in results)
    assert all(r["slug"] != "adrienne-e-adams" for r in results)  # never self


def test_co_sponsors_ordering_is_deterministic(indexed_db):
    r1 = co_sponsors(indexed_db, slug="adrienne-e-adams", min_overlap=1, limit=50)
    r2 = co_sponsors(indexed_db, slug="adrienne-e-adams", min_overlap=1, limit=50)
    assert r1 == r2  # ties broken by slug ASC, so order is stable


def test_co_sponsors_respects_min_overlap(indexed_db):
    high = co_sponsors(indexed_db, slug="adrienne-e-adams", min_overlap=99)
    assert high == []
