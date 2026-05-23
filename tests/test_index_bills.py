import json

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


def _title_hits(conn, term: str):
    return conn.execute(
        "SELECT bill_id FROM bills_fts_map m JOIN bills_fts f ON m.fts_rowid = f.rowid "
        "WHERE bills_fts MATCH ?",
        (f"title:{term}",),
    ).fetchall()


def test_index_bill_replaces_fts_content_on_reindex(tmp_path, bills_dir):
    """Re-indexing must DELETE old FTS rows, not just append.

    Contentless FTS5 silently accepts duplicate INSERTs at the same rowid, so a
    naive count-based idempotency check passes even when the DELETE is broken
    (stale terms keep matching). Mutating the title and asserting that the old
    title-only term no longer matches in the `title:` column proves the DELETE
    actually ran.
    """
    conn = init_db(tmp_path / "t.db")
    p = bills_dir / "int_0153_2022.json"
    index_bill_file(conn, p, archive_root=bills_dir.parent)

    # Sanity: original title contains "stability"; matches in the title column.
    assert len(_title_hits(conn, "stability")) == 1

    # Re-index with a mutated Title (everything else identical).
    with open(p, encoding="utf-8") as f:
        b = json.load(f)
    b["Title"] = "synthetic xyzzyvazyzzyva replacement title"
    mutated = tmp_path / "int_0153_mutated.json"
    with open(mutated, "w", encoding="utf-8") as f:
        json.dump(b, f)
    index_bill_file(conn, mutated, archive_root=tmp_path)
    conn.commit()

    # Still exactly one bills row (true PK-level idempotency).
    assert conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 1

    # New title-only marker matches — proves the new FTS row was written.
    assert len(_title_hits(conn, "xyzzyvazyzzyva")) == 1

    # Old title-only term no longer matches — proves the old FTS row was DELETEd
    # (not merely shadowed by an append).
    assert _title_hits(conn, "stability") == []
