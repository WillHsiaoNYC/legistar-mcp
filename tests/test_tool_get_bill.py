import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import get_bill


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn, fixtures_root


def test_get_bill_by_file(indexed_db):
    conn, root = indexed_db
    bill = get_bill(conn, archive_root=root, file="Int 0153-2022")
    assert bill["File"] == "Int 0153-2022"
    assert "Text" in bill
    assert "Sponsors" in bill


def test_get_bill_missing_returns_none(indexed_db):
    conn, root = indexed_db
    assert get_bill(conn, archive_root=root, file="Does Not Exist") is None


def test_get_bill_includes_legistar_url(indexed_db):
    conn, root = indexed_db
    bill = get_bill(conn, archive_root=root, file="Int 0153-2022")
    assert bill["LegistarURL"] == (
        "https://legistar.council.nyc.gov/gateway.aspx"
        f"?m=l&id=/matter.aspx?key={bill['ID']}"
    )
