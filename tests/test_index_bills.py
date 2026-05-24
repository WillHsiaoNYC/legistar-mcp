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


def test_index_bill_populates_votes_for_history_with_roll_call(tmp_path, fixtures_root):
    """Bills with History[].Votes[] entries get mirrored into votes table."""
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    # The MOO fixture int_0153_2022.json has History[] with embedded Votes[].
    # Count how many roll-call rows exist for that bill across all history records.
    rows = conn.execute(
        "SELECT COUNT(*) FROM votes WHERE bill_id = ?", (68628,)
    ).fetchone()[0]
    assert rows > 0, "MOO fixture must have at least 1 vote row"


def test_index_bill_skips_votes_without_slug_or_value(tmp_path, fixtures_root):
    """Indexer skips vote rows missing Slug or Vote; resulting votes rows have both."""
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    rows = conn.execute(
        "SELECT person_slug, vote_value FROM votes WHERE bill_id = ?", (68628,)
    ).fetchall()
    assert all(r["person_slug"] for r in rows)
    assert all(r["vote_value"] for r in rows)


def test_index_bill_votes_carry_history_metadata(tmp_path, fixtures_root):
    """Vote rows have action, vote_date, history_record_id populated from History."""
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    row = conn.execute(
        "SELECT history_record_id, action, vote_date FROM votes "
        "WHERE bill_id = ? LIMIT 1", (68628,)
    ).fetchone()
    assert row is not None
    assert row["history_record_id"] is not None
    # Action and vote_date should be non-null for roll-call records
    assert row["action"] is not None
    assert row["vote_date"] is not None
