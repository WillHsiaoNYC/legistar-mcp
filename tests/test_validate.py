import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools._validate import (
    build_fts_query,
    clamp_limit,
    clamp_offset,
    envelope,
    normalize_fts_query,
    today_nyc,
    validate_days,
    validate_iso_date,
    validate_year,
)


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_clamp_limit_bounds():
    assert clamp_limit(20) == 20
    assert clamp_limit(-1) == 1      # SQLite LIMIT -1 means unlimited — never pass it through
    assert clamp_limit(0) == 1
    assert clamp_limit(10_000) == 200
    assert clamp_limit(10_000, hi=1000) == 1000


def test_clamp_offset():
    assert clamp_offset(0) == 0
    assert clamp_offset(-5) == 0
    assert clamp_offset(40) == 40


def test_validate_year_range():
    assert validate_year("year_from", None) is None
    assert validate_year("year_from", 2024) == 2024
    with pytest.raises(ValueError, match="year_from"):
        validate_year("year_from", 24)       # 2-digit shorthand silently matched everything before
    with pytest.raises(ValueError, match="year_to"):
        validate_year("year_to", 21024)


def test_validate_iso_date_accepts_prefix_forms():
    assert validate_iso_date("date_from", None) is None
    for ok in ("2024", "2024-08", "2024-08-15", "2024-08-15T13:30:00-04:00"):
        assert validate_iso_date("date_from", ok) == ok
    for bad in ("08/15/2024", "Aug 15 2024", "2024-13", "2024-08-99", "next week"):
        with pytest.raises(ValueError, match="date_from"):
            validate_iso_date("date_from", bad)


def test_validate_iso_date_full_timestamps_validated_and_normalized():
    # Space-separated timestamps (str(datetime.now()) form) are normalized to
    # 'T' so they lex-compare correctly against stored ISO timestamps.
    assert (
        validate_iso_date("date_to", "2024-08-15 23:59:59")
        == "2024-08-15T23:59:59"
    )
    assert (
        validate_iso_date("date_to", "2024-08-15T23:59:59")
        == "2024-08-15T23:59:59"
    )
    # Garbage after a valid 10-char prefix no longer sneaks through.
    with pytest.raises(ValueError, match="date_to"):
        validate_iso_date("date_to", "2024-08-15xxxx")


def test_validate_days_rejects_nonpositive():
    assert validate_days(7) == 7
    with pytest.raises(ValueError, match="days"):
        validate_days(-7)   # negative inverted the window into the future before
    with pytest.raises(ValueError, match="days"):
        validate_days(0)


def test_today_nyc_returns_a_date():
    import datetime
    assert isinstance(today_nyc(), datetime.date)


def test_normalize_fts_query_passes_valid_and_quotes_invalid(indexed_db):
    # Valid FTS5 stays untouched.
    assert normalize_fts_query(indexed_db, "bills_fts", "housing") == "housing"
    # Punctuated natural language that FTS5 rejects gets phrase-quoted.
    assert normalize_fts_query(indexed_db, "bills_fts", "covid-19") == '"covid-19"'
    assert normalize_fts_query(indexed_db, "bills_fts", "safety (vision zero)") == '"safety (vision zero)"'
    # Whitespace-only means "no text filter".
    assert normalize_fts_query(indexed_db, "bills_fts", "   ") is None
    # Column filters valid on bills_fts but not events_fts get quoted per-table.
    assert normalize_fts_query(indexed_db, "bills_fts", "title:housing") == "title:housing"
    assert normalize_fts_query(indexed_db, "events_fts", "title:housing") == '"title:housing"'


def test_build_fts_query_combines_query_and_agency(indexed_db):
    combined = build_fts_query(indexed_db, "bills", query="housing", agency="NYPD")
    assert combined is not None
    assert "housing" in combined and " AND " in combined and "NYPD" in combined
    assert build_fts_query(indexed_db, "bills", query=None, agency=None) is None
    only_q = build_fts_query(indexed_db, "bills", query="housing", agency=None)
    assert only_q == "(housing)"


def test_envelope_shape():
    e = envelope([1, 2], total=10, offset=2)
    assert e == {"results": [1, 2], "total": 10, "offset": 2, "truncated": True}
    assert envelope([1], total=1)["truncated"] is False
