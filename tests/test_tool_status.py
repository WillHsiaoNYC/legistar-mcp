import pytest
from freezegun import freeze_time

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.status import data_status, staleness_warning


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_data_status_reports_counts_coverage_and_versions(indexed_db):
    s = data_status(indexed_db)
    assert s["counts"]["bills"] == 3
    assert s["counts"]["people"] == 1
    assert s["coverage"]["last_intro_date"] >= s["coverage"]["first_intro_date"]
    assert s["schema_version_db"] == s["schema_version_code"]
    assert s["last_indexed"] is not None
    assert s["index_age_days"] == 0
    assert s["warnings"] == []


def test_staleness_warning_after_a_week(indexed_db):
    # Freeze to a date far AFTER the fixture build so last_indexed is old.
    with freeze_time("2099-01-01"):
        w = staleness_warning(indexed_db)
        assert w is not None and "legistar-mcp index" in w
        s = data_status(indexed_db)
        assert any("legistar-mcp index" in x for x in s["warnings"])


def test_recent_and_upcoming_embed_warning_when_stale(indexed_db):
    from legistar_mcp.tools.bills import recent_bills
    from legistar_mcp.tools.events import upcoming_events
    with freeze_time("2099-01-01"):
        assert "warning" in recent_bills(indexed_db, days=30)
        assert "warning" in upcoming_events(indexed_db, days=30)
