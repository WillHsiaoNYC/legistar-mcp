import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp._db_utils import StaleIndexError
from legistar_mcp.tools.relationships import get_voting_record, vote_breakdown


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


# get_voting_record tests

def test_get_voting_record_returns_votes_for_known_person(indexed_db):
    """A person who voted in the MOO fixture should have at least one vote row."""
    # First, pick a slug that has votes. From P2 we know the MOO bill carries
    # 51 distinct voters across 2 history records.
    row = indexed_db.execute(
        "SELECT person_slug FROM votes WHERE bill_id = 68628 LIMIT 1"
    ).fetchone()
    slug = row["person_slug"]
    results = get_voting_record(indexed_db, slug=slug)
    assert results
    assert all("vote_value" in r for r in results)


def test_get_voting_record_filters_by_year(indexed_db):
    row = indexed_db.execute(
        "SELECT person_slug FROM votes WHERE bill_id = 68628 LIMIT 1"
    ).fetchone()
    slug = row["person_slug"]
    # MOO fixture votes are from 2022 (per the fixture data).
    results = get_voting_record(indexed_db, slug=slug, year_from=2022, year_to=2022)
    assert all(r["vote_date"] is None or r["vote_date"].startswith("2022") for r in results)


def test_get_voting_record_filters_by_vote_value(indexed_db):
    row = indexed_db.execute(
        "SELECT person_slug FROM votes WHERE bill_id = 68628 AND vote_value = 'Affirmative' LIMIT 1"
    ).fetchone()
    slug = row["person_slug"]
    results = get_voting_record(indexed_db, slug=slug, vote_value="Affirmative")
    assert results
    assert all(r["vote_value"] == "Affirmative" for r in results)


def test_get_voting_record_includes_bill_context(indexed_db):
    row = indexed_db.execute(
        "SELECT person_slug FROM votes WHERE bill_id = 68628 LIMIT 1"
    ).fetchone()
    slug = row["person_slug"]
    results = get_voting_record(indexed_db, slug=slug)
    moo_hits = [r for r in results if r["bill_id"] == 68628]
    assert moo_hits
    assert moo_hits[0]["file"] == "Int 0153-2022"


def test_get_voting_record_raises_stale_index_when_votes_empty(indexed_db):
    indexed_db.execute("DELETE FROM votes")
    row = indexed_db.execute("SELECT slug FROM people LIMIT 1").fetchone()
    slug = row["slug"] if row else "any-slug"
    with pytest.raises(StaleIndexError, match="--full"):
        get_voting_record(indexed_db, slug=slug)


# vote_breakdown tests

def test_vote_breakdown_returns_all_voters_for_bill(indexed_db):
    """MOO fixture has 57 vote rows for bill 68628."""
    results = vote_breakdown(indexed_db, bill_id=68628)
    assert len(results) == 57


def test_vote_breakdown_includes_vote_value(indexed_db):
    results = vote_breakdown(indexed_db, bill_id=68628)
    assert results
    assert all("vote_value" in r for r in results)
    # The fixture has affirmatives at minimum
    values = {r["vote_value"] for r in results}
    assert "Affirmative" in values


def test_vote_breakdown_unknown_bill_returns_empty(indexed_db):
    assert vote_breakdown(indexed_db, bill_id=99999999) == []


def test_vote_breakdown_raises_stale_index_when_votes_empty(indexed_db):
    indexed_db.execute("DELETE FROM votes")
    with pytest.raises(StaleIndexError, match="--full"):
        vote_breakdown(indexed_db, bill_id=68628)
