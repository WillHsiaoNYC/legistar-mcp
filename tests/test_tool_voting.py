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


def test_get_voting_record_year_to_includes_dec_31(indexed_db):
    """A vote on 2024-12-31 (full ISO timestamp) must be returned by
    year_to=2024. Old code compared against "2024-12-31" lex which
    excluded any timestamp suffix."""
    indexed_db.execute(
        "INSERT INTO votes "
        "(history_record_id, person_slug, bill_id, event_id, vote_value, "
        " vote_date, action, passed_flag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (999001, "synthetic-slug", 68628, None, "Affirmative",
         "2024-12-31T23:59:59Z", "Vote", 1),
    )
    indexed_db.commit()
    results = get_voting_record(indexed_db, slug="synthetic-slug", year_to=2024)
    assert any(r["vote_date"] == "2024-12-31T23:59:59Z" for r in results)


def test_get_voting_record_raises_stale_index_when_votes_empty(indexed_db):
    # Simulate post-upgrade pre-`--full` state: schema rolled back below
    # SCHEMA_VERSION. user_version is now the source of truth for staleness.
    indexed_db.execute("DELETE FROM votes")
    indexed_db.execute("PRAGMA user_version = 1")
    row = indexed_db.execute("SELECT slug FROM people LIMIT 1").fetchone()
    slug = row["slug"] if row else "any-slug"
    with pytest.raises(StaleIndexError, match="--full"):
        get_voting_record(indexed_db, slug=slug)


# vote_breakdown tests

def test_vote_breakdown_returns_all_voters_for_bill(indexed_db):
    """All vote rows for the fixture bill come back. The 57-row fixture
    fits comfortably under the default limit=100.
    """
    expected = indexed_db.execute(
        "SELECT COUNT(*) FROM votes WHERE bill_id = 68628"
    ).fetchone()[0]
    results = vote_breakdown(indexed_db, bill_id=68628)
    assert len(results) == expected


def test_vote_breakdown_respects_limit(indexed_db):
    results = vote_breakdown(indexed_db, bill_id=68628, limit=5)
    assert len(results) <= 5


def test_vote_breakdown_places_null_vote_date_last(indexed_db):
    """A vote row with NULL vote_date must appear after dated rows even
    under DESC vote_date ordering (SQLite's default would put NULL first).
    """
    indexed_db.execute(
        "INSERT INTO votes "
        "(history_record_id, person_slug, bill_id, event_id, vote_value, "
        " vote_date, action, passed_flag) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (888888, "null-date-voter", 68628, None, "Affirmative", None, "Filed", 1),
    )
    indexed_db.commit()
    results = vote_breakdown(indexed_db, bill_id=68628, limit=200)
    # Find the null-date row's position.
    null_idx = next(
        i for i, r in enumerate(results) if r["person_slug"] == "null-date-voter"
    )
    # No dated row appears AFTER the null row.
    for r in results[null_idx + 1:]:
        assert r["vote_date"] is None


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
    # Simulate post-upgrade pre-`--full` state: schema rolled back below
    # SCHEMA_VERSION. user_version is now the source of truth for staleness.
    indexed_db.execute("DELETE FROM votes")
    indexed_db.execute("PRAGMA user_version = 1")
    with pytest.raises(StaleIndexError, match="--full"):
        vote_breakdown(indexed_db, bill_id=68628)
