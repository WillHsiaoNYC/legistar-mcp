import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import aggregate_bills


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_aggregate_bills_groups_by_status(indexed_db):
    rows = aggregate_bills(indexed_db, group_by=["status_name"])["results"]
    assert isinstance(rows, list)
    assert all("count" in r for r in rows)
    assert all("status_name" in r for r in rows)
    assert sum(r["count"] for r in rows) >= 2


def test_aggregate_bills_multi_dim_group_by(indexed_db):
    rows = aggregate_bills(indexed_db, group_by=["status_name", "type_name"])["results"]
    assert all("status_name" in r and "type_name" in r and "count" in r for r in rows)


def test_aggregate_bills_intro_year_returns_integer(indexed_db):
    rows = aggregate_bills(indexed_db, group_by=["intro_year"])["results"]
    assert rows
    assert all(isinstance(r["intro_year"], int) for r in rows)


def test_aggregate_bills_rejects_unknown_group_by(indexed_db):
    with pytest.raises(ValueError):
        aggregate_bills(indexed_db, group_by=["nonexistent"])


def test_aggregate_bills_intro_year_excludes_null_intro_date(indexed_db):
    """A bill with no intro_date can't be bucketed into a year — it must not
    produce a spurious {'intro_year': None} group, and every intro_year must
    stay an int."""
    indexed_db.execute(
        "INSERT INTO bills (id, file, intro_date, path) VALUES (?, ?, NULL, ?)",
        (980001, "Int 8888-2024", "bills/nulldate.json"),
    )
    indexed_db.commit()
    rows = aggregate_bills(indexed_db, group_by=["intro_year"])["results"]
    assert rows
    assert all(r["intro_year"] is not None for r in rows)
    assert all(isinstance(r["intro_year"], int) for r in rows)


def test_aggregate_bills_sponsor_slug_excludes_unsponsored_bills(indexed_db):
    """A bill with no sponsors rows has no slug to bucket under — it must not
    produce a spurious {'sponsor_slug': None} group (same rationale as the
    intro_year NULL-date exclusion above; the LEFT JOIN makes NULLs
    structural, not data-quality)."""
    indexed_db.execute(
        "INSERT INTO bills (id, file, intro_date, path) VALUES (?, ?, ?, ?)",
        (970001, "Int 7777-2024", "2024-05-01T00:00:00Z", "bills/nosponsor.json"),
    )
    indexed_db.commit()
    rows = aggregate_bills(indexed_db, group_by=["sponsor_slug"])["results"]
    assert rows
    assert all(r["sponsor_slug"] is not None for r in rows)


def test_aggregate_bills_year_to_9999_is_rejected(indexed_db):
    """year_to=9999 was a 'no upper bound' sentinel at the year_window layer,
    but validate_year now rejects any year outside [1900, 2100] before the
    query is built — an out-of-range sentinel raises with guidance instead of
    silently returning everything."""
    unfiltered = aggregate_bills(indexed_db, group_by=["intro_year"])["results"]
    assert unfiltered
    with pytest.raises(ValueError, match="4-digit year"):
        aggregate_bills(indexed_db, group_by=["intro_year"], year_to=9999)


def test_aggregate_bills_year_to_includes_dec_31(indexed_db):
    """A bill introduced 2024-12-31 (stored as a full ISO timestamp) must be
    counted by year_to=2024. Old code compared against the date-only string
    "2024-12-31" which lex-excludes the full timestamp."""
    indexed_db.execute(
        "INSERT INTO bills (id, file, intro_date, path) VALUES (?, ?, ?, ?)",
        (999001, "Int 9999-2024", "2024-12-31T23:59:59Z", "bills/synthetic.json"),
    )
    indexed_db.commit()
    rows = aggregate_bills(indexed_db, group_by=["intro_year"], year_to=2024)["results"]
    by_year = {r["intro_year"]: r["count"] for r in rows}
    assert 2024 in by_year
    # Total includes our synthetic Dec 31 bill + at least the existing 2024 fixture.
    assert by_year[2024] >= 2


def test_aggregate_bills_accepts_free_text_query(indexed_db):
    from legistar_mcp.tools.bills import aggregate_bills
    rows = aggregate_bills(indexed_db, group_by=["intro_year"], query="domestic violence")["results"]
    assert rows, "FTS-filtered aggregate should find the 0153-2022 fixture"
    assert all("intro_year" in r and "count" in r for r in rows)
