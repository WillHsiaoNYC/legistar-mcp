from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all


def test_build_all_indexes_fixtures(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    stats = build_all(conn, archive_root=fixtures_root)
    assert stats["bills"] >= 2
    assert stats["events"] >= 1
    assert stats["people"] >= 1
    n = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
    assert n == stats["bills"]


def test_build_all_incremental_skips_unchanged(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    s1 = build_all(conn, archive_root=fixtures_root)
    s2 = build_all(conn, archive_root=fixtures_root, incremental=True)
    assert s1["bills"] > 0
    assert s2["bills"] == 0   # second pass: nothing new
