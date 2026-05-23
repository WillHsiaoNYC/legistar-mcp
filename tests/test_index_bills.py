from legistar_mcp.db import init_db
from legistar_mcp.index.build import index_bill_file


def test_index_bill_inserts_row_and_fts(tmp_path, bills_dir):
    conn = init_db(tmp_path / "t.db")
    index_bill_file(conn, bills_dir / "int_0153_2022.json", archive_root=bills_dir.parent)
    conn.commit()

    row = conn.execute("SELECT file, title, intro_date FROM bills").fetchone()
    assert row["file"] == "Int 0153-2022"
    assert "housing stability" in row["title"].lower()
    assert row["intro_date"].startswith("2022")

    hits = conn.execute(
        "SELECT bill_id FROM bills_fts_map m "
        "JOIN bills_fts f ON m.fts_rowid = f.rowid "
        "WHERE bills_fts MATCH ?",
        ('"mayor\'s office of operations"',),
    ).fetchall()
    assert len(hits) == 1


def test_index_bill_is_idempotent(tmp_path, bills_dir):
    conn = init_db(tmp_path / "t.db")
    p = bills_dir / "int_0153_2022.json"
    index_bill_file(conn, p, archive_root=bills_dir.parent)
    index_bill_file(conn, p, archive_root=bills_dir.parent)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
    assert count == 1
