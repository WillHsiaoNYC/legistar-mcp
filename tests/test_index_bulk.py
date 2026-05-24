import pytest
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
    columns are populated. user_version must stay where it was (i.e., a second
    incremental pass over an already-current DB keeps user_version at
    SCHEMA_VERSION, doesn't downgrade or otherwise change it)."""
    conn = init_db(tmp_path / "t.db")
    # First seed with a full reindex (so user_version == SCHEMA_VERSION and
    # incremental is allowed).
    build_all(conn, archive_root=fixtures_root)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    # Second pass: incremental must not touch user_version one way or the other.
    build_all(conn, archive_root=fixtures_root, incremental=True)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_build_all_refuses_incremental_when_user_version_stale(tmp_path, fixtures_root):
    """Simulates upgrading the package (SCHEMA_VERSION bumps) but running the
    default `legistar-mcp index ...` (incremental). Without a guard, only
    LastModified-changed files would get the new schema's mirror writes — the
    rest of the archive would silently stay empty. build_all must refuse."""
    conn = init_db(tmp_path / "t.db")
    # Force user_version to an older value, simulating an upgrade.
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    with pytest.raises(RuntimeError, match="Re-run with --full"):
        build_all(conn, archive_root=fixtures_root, incremental=True)


def test_build_all_allows_full_when_user_version_stale(tmp_path, fixtures_root):
    """Same stale-version setup, but --full is allowed (and is in fact the
    user's escape hatch from the refusal above)."""
    conn = init_db(tmp_path / "t.db")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    # Must not raise.
    stats = build_all(conn, archive_root=fixtures_root, incremental=False)
    assert stats["bills"] >= 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
