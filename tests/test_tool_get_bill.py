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


def test_get_bill_missing_raises_value_error(indexed_db):
    conn, root = indexed_db
    with pytest.raises(ValueError, match="search_bills"):
        get_bill(conn, archive_root=root, file="Does Not Exist")


def test_get_bill_includes_legistar_url(indexed_db):
    conn, root = indexed_db
    bill = get_bill(conn, archive_root=root, file="Int 0153-2022")
    assert bill["LegistarURL"] == (
        "https://legistar.council.nyc.gov/gateway.aspx"
        f"?m=l&id=/matter.aspx?key={bill['ID']}"
    )


def test_get_bill_text_targeted_passage(indexed_db):
    conn, root = indexed_db
    from legistar_mcp.tools.bills import get_bill_text

    out = get_bill_text(
        conn, root, file="Int 0153-2022",
        query="office of operations", context_chars=200,
    )
    assert out["file"] == "Int 0153-2022"
    assert out["segments"], "phrase occurs in the bill text"
    assert all("office of operations" in s["text"].lower() for s in out["segments"])
    assert all(len(s["text"]) <= 200 * 2 + len("office of operations") for s in out["segments"])


def test_get_bill_text_head_mode_bounds_output(indexed_db):
    conn, root = indexed_db
    from legistar_mcp.tools.bills import get_bill_text

    out = get_bill_text(conn, root, file="Int 0153-2022", context_chars=500)
    assert len(out["segments"]) == 1
    assert out["segments"][0]["offset"] == 0
    assert len(out["segments"][0]["text"]) <= 1000
    assert out["total_chars"] > 0


def test_get_bill_text_no_match_returns_empty_segments(indexed_db):
    conn, root = indexed_db
    from legistar_mcp.tools.bills import get_bill_text

    out = get_bill_text(conn, root, file="Int 0153-2022", query="zzzunfindable")
    assert out["segments"] == [] and out["total_chars"] > 0
