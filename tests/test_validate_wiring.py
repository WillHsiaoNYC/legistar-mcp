"""Wiring tests: validation helpers must actually be applied inside each tool.
Uses the shared fixtures archive (3 bills, 1 event, 1 person)."""
import pytest
from freezegun import freeze_time

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
    assert len(search_bills(indexed_db, limit=-1)["results"]) == 1
    assert len(search_people(indexed_db, limit=-1)["results"]) == 1
    assert len(search_events(indexed_db, limit=-1)["results"]) == 1
    assert len(recent_bills(indexed_db, days=36500, limit=-1)["results"]) == 1
    assert len(upcoming_events(indexed_db, days=36500, limit=-1)["results"]) <= 1
    assert len(vote_breakdown(indexed_db, bill_id=68628, limit=-1)["results"]) <= 1


def test_punctuated_query_does_not_crash(indexed_db):
    # These previously raised sqlite3.OperationalError from FTS5.
    for q in ("covid-19", "don't", "safety (vision zero)", "   "):
        search_bills(indexed_db, query=q, limit=5)
        search_events(indexed_db, query=q, limit=5)


def test_query_and_agency_combine_instead_of_override(indexed_db):
    # Fixture Int 0153-2022 mentions the Mayor's Office of Operations.
    # agency alone matches it; adding an unrelated query must NARROW, not override.
    agency_only = search_bills(
        indexed_db, agency="Mayor's Office of Operations", limit=5
    )["results"]
    assert any("0153-2022" in r["file"] for r in agency_only)
    combined = search_bills(
        indexed_db, query="zzzunfindable", agency="Mayor's Office of Operations", limit=5
    )["results"]
    assert combined == []  # query was previously discarded → would return the agency hits


def test_bad_year_and_date_and_days_raise_guidance(indexed_db):
    from legistar_mcp.tools.bills import aggregate_bills
    from legistar_mcp.tools.committees import list_committees
    from legistar_mcp.tools.relationships import get_voting_record

    with pytest.raises(ValueError, match="4-digit year"):
        search_bills(indexed_db, year_from=24)          # previously: filter silently ignored
    with pytest.raises(ValueError, match="4-digit year"):
        aggregate_bills(indexed_db, group_by=["intro_year"], year_to=24)
    with pytest.raises(ValueError, match="4-digit year"):
        list_committees(indexed_db, year_from=99)
    with pytest.raises(ValueError, match="4-digit year"):
        get_voting_record(indexed_db, slug="adrienne-e-adams", year_from=24)
    with pytest.raises(ValueError, match="ISO format"):
        search_events(indexed_db, date_from="08/15/2024")  # previously: matched everything
    with pytest.raises(ValueError, match="ISO format"):
        search_events(indexed_db, date_to="Aug 15")
    with pytest.raises(ValueError, match="days"):
        recent_bills(indexed_db, days=-7)               # previously: future-window inversion
    with pytest.raises(ValueError, match="days"):
        upcoming_events(indexed_db, days=0)


@freeze_time("2024-03-13 01:00:00")  # 01:00 UTC = 21:00 Mar 12 in NYC
def test_windows_use_nyc_calendar_day(indexed_db):
    """A UTC server just after NYC evening must still treat 'today' as Mar 12.
    recent_bills(days=33) from Mar 12 reaches back to Feb 8 and catches the
    Int 0001-2024 fixture (intro 2024-02-08); from a UTC 'today' of Mar 13 the
    same window starts Feb 9 and misses it."""
    results = recent_bills(indexed_db, days=33, limit=10)["results"]
    assert any("0001-2024" in r["file"] for r in results)


def test_unknown_identifiers_raise_guided_errors(indexed_db, fixtures_root):
    from legistar_mcp.tools.bills import get_bill
    from legistar_mcp.tools.events import get_bill_hearings, get_event
    from legistar_mcp.tools.people import get_person
    from legistar_mcp.tools.relationships import co_sponsors, get_voting_record

    with pytest.raises(ValueError, match="search_bills"):
        get_bill(indexed_db, fixtures_root, file="Int 9999-2099")
    with pytest.raises(ValueError, match="search_events"):
        get_event(indexed_db, fixtures_root, id=999999999)
    with pytest.raises(ValueError, match="search_people"):
        get_person(indexed_db, fixtures_root, "nobody-here")
    with pytest.raises(ValueError, match="search_bills"):
        get_bill_hearings(indexed_db, file="Int 9999-2099")
    with pytest.raises(ValueError, match="search_people"):
        get_voting_record(indexed_db, slug="nobody-here")
    with pytest.raises(ValueError, match="search_people"):
        co_sponsors(indexed_db, slug="nobody-here")


def test_vote_breakdown_accepts_file(indexed_db):
    from legistar_mcp.tools.relationships import vote_breakdown
    by_file = vote_breakdown(indexed_db, file="Int 0153-2022")["results"]
    assert isinstance(by_file, list)
    with pytest.raises(ValueError, match="search_bills"):
        vote_breakdown(indexed_db, file="Int 9999-2099")


def test_missing_archive_file_is_guided_not_traceback(indexed_db, tmp_path, fixtures_root):
    from legistar_mcp.tools.bills import get_bill
    # Point the reader at a root where the indexed rel-path doesn't exist.
    with pytest.raises(ValueError, match="legistar-mcp index"):
        get_bill(indexed_db, tmp_path, file="Int 0153-2022")


def test_filters_match_case_insensitively(indexed_db):
    exact = search_bills(indexed_db, status="Enacted", limit=10)["results"]
    lower = search_bills(indexed_db, status="enacted", limit=10)["results"]
    assert [r["file"] for r in lower] == [r["file"] for r in exact]
    assert lower, "fixture set contains an Enacted bill"


def test_search_people_matches_across_middle_initial(indexed_db):
    # full_name is 'Adrienne E. Adams' — a single-substring LIKE missed this.
    hits = search_people(indexed_db, name="Adrienne Adams")["results"]
    assert any(p["slug"] == "adrienne-e-adams" for p in hits)


def test_snippet_offsets_survive_unicode_case_folding():
    from legistar_mcp.tools._snippet import _build_snippet
    # 'İ' (U+0130) lowercases to TWO chars — index math on text.lower()
    # previously misplaced the <mark>.
    text = "İİİİİ the police department shall report annually İİİİİ"
    snip = _build_snippet(text, ["police department"])
    assert snip is not None
    assert "<mark>police department</mark>" in snip


def test_plain_query_search_returns_mentions(indexed_db):
    rows = search_bills(indexed_db, query='"domestic violence"', limit=5)["results"]
    hit = next(r for r in rows if "0153-2022" in r["file"])
    assert hit["mentions"], "plain-text query should carry role-context snippets too"
    rows_bare = search_bills(indexed_db, query="domestic violence", limit=5)["results"]
    hit_bare = next(r for r in rows_bare if "0153-2022" in r["file"])
    assert hit_bare["mentions"]


def test_unknown_numeric_bill_id_raises_not_empty(indexed_db):
    """resolve_bill_id must verify the numeric-id branch too — a hallucinated
    bill_id previously returned a silent [] from vote_breakdown/get_bill_hearings."""
    from legistar_mcp.tools.events import get_bill_hearings

    with pytest.raises(ValueError, match="search_bills"):
        vote_breakdown(indexed_db, bill_id=999999999)
    with pytest.raises(ValueError, match="search_bills"):
        get_bill_hearings(indexed_db, id=999999999)


def test_search_bills_envelope_and_offset(indexed_db):
    page1 = search_bills(indexed_db, limit=2)
    assert set(page1) == {"results", "total", "offset", "truncated"}
    assert page1["total"] == 3 and len(page1["results"]) == 2 and page1["truncated"]
    page2 = search_bills(indexed_db, limit=2, offset=2)
    assert len(page2["results"]) == 1 and page2["offset"] == 2
    assert not page2["truncated"]
    ids = {r["id"] for r in page1["results"]} | {r["id"] for r in page2["results"]}
    assert len(ids) == 3  # pages don't overlap


def test_aggregate_empty_group_by_is_grand_total(indexed_db):
    from legistar_mcp.tools.bills import aggregate_bills
    out = aggregate_bills(indexed_db, group_by=[])
    assert out["results"] == [{"count": 3}]
