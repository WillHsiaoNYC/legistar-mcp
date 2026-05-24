from legistar_mcp.db import SCHEMA_VERSION, init_db
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


def test_build_all_full_bumps_user_version(tmp_path, fixtures_root):
    """A full reindex must set user_version to the current SCHEMA_VERSION so
    the stale-data warning clears."""
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)  # incremental=False is default
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_build_all_incremental_does_not_bump_user_version(tmp_path, fixtures_root):
    """Incremental skips unchanged files, so it can't guarantee new tables /
    columns are populated. user_version must stay where it was."""
    conn = init_db(tmp_path / "t.db")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    build_all(conn, archive_root=fixtures_root, incremental=True)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
