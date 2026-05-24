import pytest
from freezegun import freeze_time

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import recent_bills, search_bills


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_search_by_keyword(indexed_db):
    results = search_bills(indexed_db, query="domestic violence", limit=5)
    assert any("0153-2022" in r["file"] for r in results)


def test_search_filters_by_year(indexed_db):
    results = search_bills(indexed_db, query=None, year_from=2024, limit=5)
    assert all(r["intro_date"] >= "2024" for r in results)


def test_search_limit_caps_results(indexed_db):
    results = search_bills(indexed_db, query=None, limit=1)
    assert len(results) == 1


def test_search_results_include_legistar_url(indexed_db):
    results = search_bills(indexed_db, query="domestic violence", limit=5)
    hit = next(r for r in results if "0153-2022" in r["file"])
    assert hit["legistar_url"] == (
        "https://legistar.council.nyc.gov/LegislationDetail.aspx"
        f"?ID={hit['id']}&GUID=13DF2614-9622-473B-BDEE-4775812DEEAF"
    )
    # The transient guid column from the SELECT must not leak into output.
    assert "guid" not in hit


def test_agency_query_returns_snippets_with_role_context(indexed_db):
    results = search_bills(
        indexed_db,
        agency="Mayor's Office of Operations",
        year_from=2022,
        limit=5,
    )
    assert any("0153-2022" in r["file"] for r in results)
    hit = next(r for r in results if "0153-2022" in r["file"])
    assert "mentions" in hit
    assert len(hit["mentions"]) >= 1
    # Snippet must contain MOO + enough context to characterize role
    joined = " ".join(m["snippet"].lower() for m in hit["mentions"])
    assert "office of operations" in joined
    # Should mention SOME role-indicating word
    assert any(
        w in joined for w in ("consultation", "report", "submit", "established", "shall")
    )


@freeze_time("2024-04-01")
def test_recent_bills_within_window(indexed_db):
    results = recent_bills(indexed_db, days=60, limit=10)
    assert any("0001-2024" in r["file"] for r in results)


@freeze_time("2024-04-01")
def test_recent_bills_empty_window(indexed_db):
    assert recent_bills(indexed_db, days=1) == []


@freeze_time("2024-04-01")
def test_recent_bills_have_legistar_url(indexed_db):
    results = recent_bills(indexed_db, days=60)
    assert results and "legistar_url" in results[0]


@freeze_time("2024-04-01")
def test_recent_bills_status_filter_passes_through(indexed_db):
    enacted = recent_bills(indexed_db, days=60, status="Enacted")
    for r in enacted:
        assert r["status_name"] == "Enacted"
    bogus = recent_bills(indexed_db, days=60, status="Nonexistent Status")
    assert bogus == []


@freeze_time("2024-04-01")
def test_recent_bills_type_filter_passes_through(indexed_db):
    intros = recent_bills(indexed_db, days=60, type="Introduction")
    for r in intros:
        assert r["type_name"] == "Introduction"
