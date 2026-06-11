"""Unit tests for the shared aggregation helpers in tools/_aggregate.py.

These back the de-duplication of aggregate_bills / aggregate_events and the
date-boundary fix for events. Pure-function tests — no DB needed.
"""
import pytest

from legistar_mcp.tools._aggregate import (
    date_upper_bound,
    fts_join,
    validate_group_by,
    year_window,
)


# --- fts_join -------------------------------------------------------------

def test_fts_join_builds_map_and_content_joins():
    joins, match = fts_join("events", "event_id")
    assert joins == [
        "JOIN events_fts_map m ON events.id = m.event_id",
        "JOIN events_fts f ON m.fts_rowid = f.rowid",
    ]
    assert match == "events_fts MATCH ?"


def test_fts_join_uses_table_specific_id_column():
    joins, match = fts_join("bills", "bill_id")
    assert joins[0] == "JOIN bills_fts_map m ON bills.id = m.bill_id"
    assert match == "bills_fts MATCH ?"


# --- validate_group_by ---------------------------------------------------

def test_validate_group_by_accepts_known_dims():
    # No exception for a subset of the allowed set.
    validate_group_by(["a", "b"], {"a", "b", "c"})


def test_validate_group_by_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        validate_group_by([], {"a"})


def test_validate_group_by_rejects_unknown_dim():
    with pytest.raises(ValueError, match="unsupported"):
        validate_group_by(["a", "zzz"], {"a", "b"})


# --- year_window ----------------------------------------------------------

def test_year_window_none_none_is_empty():
    clauses, params = year_window("intro_date", None, None)
    assert clauses == []
    assert params == []


def test_year_window_from_only_is_inclusive_jan_1():
    clauses, params = year_window("intro_date", 2024, None)
    assert clauses == ["intro_date >= ?"]
    assert params == ["2024-01-01"]


def test_year_window_to_only_uses_exclusive_next_year():
    # year_to=2024 must become an exclusive < 2025-01-01 so a Dec-31 full
    # ISO timestamp isn't lex-excluded.
    clauses, params = year_window("intro_date", None, 2024)
    assert clauses == ["intro_date < ?"]
    assert params == ["2025-01-01"]


def test_year_window_both_bounds():
    clauses, params = year_window("date", 2022, 2024)
    assert clauses == ["date >= ?", "date < ?"]
    assert params == ["2022-01-01", "2025-01-01"]


def test_year_window_year_zero_is_not_treated_as_missing():
    # A truthy check (`if year_from:`) would drop year 0; we use `is not None`.
    clauses, params = year_window("d", 0, None)
    assert clauses == ["d >= ?"]
    assert params == ["0000-01-01"]


def test_year_window_pads_years_below_1000():
    # Unpadded "999-01-01" lex-sorts AFTER every real "19xx"/"20xx" date
    # ('9' > '2'), silently excluding all rows. Years must be zero-padded.
    clauses, params = year_window("d", 999, None)
    assert params == ["0999-01-01"]


def test_year_window_to_pads_next_year_boundary():
    clauses, params = year_window("d", None, 998)
    assert clauses == ["d < ?"]
    assert params == ["0999-01-01"]


def test_year_window_year_to_9999_means_unbounded():
    # There is no representable next-year boundary above 9999, and no ISO
    # date can exceed it — emit no upper clause instead of the lex-broken
    # "10000-01-01" (which sorts BEFORE "2024-..." and excludes everything).
    clauses, params = year_window("d", None, 9999)
    assert clauses == []
    assert params == []
    clauses, params = year_window("d", 2020, 9999)
    assert clauses == ["d >= ?"]
    assert params == ["2020-01-01"]


# --- date_upper_bound -----------------------------------------------------

def test_date_upper_bound_date_only_covers_whole_day():
    # A bare YYYY-MM-DD must become an exclusive next-day bound so same-day
    # full-timestamp rows ("2024-08-15T13:30:00-04:00") are included.
    clause, param = date_upper_bound("events.date", "2024-08-15")
    assert clause == "events.date < ?"
    assert param == "2024-08-16"


def test_date_upper_bound_month_end_rolls_over():
    clause, param = date_upper_bound("events.date", "2024-12-31")
    assert clause == "events.date < ?"
    assert param == "2025-01-01"


def test_date_upper_bound_full_timestamp_compared_directly():
    # When the caller passes a full timestamp, honor it verbatim with <=.
    ts = "2024-08-15T13:30:00-04:00"
    clause, param = date_upper_bound("events.date", ts)
    assert clause == "events.date <= ?"
    assert param == ts


def test_date_upper_bound_non_date_ten_chars_falls_back_to_lte():
    # len==10 but not a real date: don't crash, fall back to direct compare.
    clause, param = date_upper_bound("events.date", "2024-13-99")
    assert clause == "events.date <= ?"
    assert param == "2024-13-99"


def test_date_upper_bound_max_iso_date_does_not_crash():
    # date.max + 1 day raises OverflowError (NOT ValueError) — a bare
    # `except ValueError` turns the natural "no upper bound" sentinel into
    # a tool crash. Must fall back to direct compare instead.
    clause, param = date_upper_bound("events.date", "9999-12-31")
    assert clause == "events.date <= ?"
    assert param == "9999-12-31"


def test_date_upper_bound_month_prefix_covers_whole_month():
    # date_to='2024-08' means "through August"; the lex fallback
    # `<= '2024-08'` would exclude every timestamp IN August.
    clause, param = date_upper_bound("events.date", "2024-08")
    assert clause == "events.date < ?"
    assert param == "2024-09-01"


def test_date_upper_bound_december_prefix_rolls_to_next_year():
    clause, param = date_upper_bound("events.date", "2024-12")
    assert clause == "events.date < ?"
    assert param == "2025-01-01"


def test_date_upper_bound_year_prefix_covers_whole_year():
    clause, param = date_upper_bound("events.date", "2024")
    assert clause == "events.date < ?"
    assert param == "2025-01-01"


def test_date_upper_bound_non_month_seven_chars_falls_back_to_lte():
    clause, param = date_upper_bound("events.date", "2024-13")
    assert clause == "events.date <= ?"
    assert param == "2024-13"


def test_date_upper_bound_year_9999_prefix_falls_back_to_lte():
    # No representable next-year boundary — direct compare, no crash.
    clause, param = date_upper_bound("events.date", "9999")
    assert clause == "events.date <= ?"
    assert param == "9999"
