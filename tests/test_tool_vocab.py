import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.vocab import list_vocabulary


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_list_vocabulary_status_returns_known_values(indexed_db):
    values = list_vocabulary(indexed_db, "status_name")
    assert isinstance(values, list)
    assert values
    assert all(isinstance(v, str) for v in values)
    assert "Enacted" in values


def test_list_vocabulary_rejects_unknown_field(indexed_db):
    with pytest.raises(ValueError):
        list_vocabulary(indexed_db, "nonexistent")


def test_list_vocabulary_event_committee_returns_known_values(indexed_db):
    values = list_vocabulary(indexed_db, "event_committee")
    assert isinstance(values, list)
    assert all(isinstance(v, str) for v in values)
    assert "City Council" in values


def test_list_agencies_returns_catalog_and_filters():
    from legistar_mcp.tools.vocab import list_agencies
    out = list_agencies()
    assert out["total"] >= 90 and not out["truncated"]
    nypd = [a for a in out["results"] if a["slug"] == "nypd"]
    assert nypd and "NYPD" in nypd[0]["aliases"]
    filtered = list_agencies(query="consumer")
    assert filtered["total"] < out["total"]
    assert any(a["slug"] == "dcwp" for a in filtered["results"])
