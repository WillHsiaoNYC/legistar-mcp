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
    # Seed real data first so the DB is populated-but-stale: a fresh/empty DB
    # now auto-promotes to a full build (Task 1), so only a populated DB
    # actually exercises the stale-schema guard.
    build_all(conn, archive_root=fixtures_root)
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


def test_incremental_on_fresh_db_succeeds_and_stamps_version(tmp_path, fixtures_root):
    """README quickstart uses the CLI default (--incremental) on a brand-new DB.
    A fresh DB must auto-promote to a full build instead of raising."""
    conn = init_db(tmp_path / "fresh.db")
    stats = build_all(conn, archive_root=fixtures_root, incremental=True)
    assert stats["bills"] > 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_empty_archive_dir_errors_instead_of_silent_success(tmp_path):
    """Pointing --archive at an existing-but-wrong directory previously
    'succeeded' with bills=0 and persisted the junk path. It must error."""
    conn = init_db(tmp_path / "t.db")
    wrong_dir = tmp_path / "not_an_archive"
    wrong_dir.mkdir()
    with pytest.raises(RuntimeError, match="No archive content"):
        build_all(conn, archive_root=wrong_dir, incremental=False)
    # Nothing persisted: no recorded archive_root, version not bumped.
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'archive_root'"
    ).fetchone()
    assert row is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0


def test_incremental_indexes_files_without_lastmodified(tmp_path, fixtures_root):
    """A record with no LastModified previously compared None == None against
    the 'seen' map and was skipped forever — never indexed at all."""
    import json
    import shutil

    archive = tmp_path / "archive"
    shutil.copytree(fixtures_root, archive)
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=archive, incremental=False)

    src = json.loads((archive / "bills" / "int_0001_2024.json").read_text())
    src["ID"] = 999001
    src["File"] = "Int 9990-2024"
    src.pop("LastModified", None)
    (archive / "bills" / "no_lastmod.json").write_text(json.dumps(src))

    build_all(conn, archive_root=archive, incremental=True)
    row = conn.execute("SELECT file FROM bills WHERE id = 999001").fetchone()
    assert row is not None and row["file"] == "Int 9990-2024"


def test_removed_archive_files_are_purged_on_reindex(tmp_path, fixtures_root):
    """Upstream deletes/renames a JSON → the row previously survived every
    reindex (even --full), leaving phantom bills searchable forever."""
    import shutil

    archive = tmp_path / "archive"
    shutil.copytree(fixtures_root, archive)
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=archive, incremental=False)
    assert conn.execute(
        "SELECT 1 FROM bills WHERE file = 'Int 0153-2022'"
    ).fetchone() is not None

    (archive / "bills" / "int_0153_2022.json").unlink()
    stats = build_all(conn, archive_root=archive, incremental=False)

    assert stats["removed"] >= 1
    assert conn.execute(
        "SELECT 1 FROM bills WHERE file = 'Int 0153-2022'"
    ).fetchone() is None
    # Cascade: no orphaned sponsors/votes/FTS-map rows may remain.
    assert conn.execute(
        "SELECT COUNT(*) FROM sponsors s LEFT JOIN bills b ON s.bill_id = b.id "
        "WHERE b.id IS NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM bills_fts_map m LEFT JOIN bills b ON m.bill_id = b.id "
        "WHERE b.id IS NULL"
    ).fetchone()[0] == 0
    # FTS content is gone too: an FTS-backed search must no longer surface the
    # purged bill (behavioral check — robust even if other fixtures also match
    # the phrase).
    from legistar_mcp.tools.bills import search_bills
    remaining = search_bills(conn, query='"domestic violence"', limit=10)["results"]
    assert not any("0153-2022" in r["file"] for r in remaining)


def test_build_all_records_last_indexed(tmp_path, fixtures_root):
    import datetime
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'last_indexed'"
    ).fetchone()
    assert row is not None
    stamp = datetime.datetime.fromisoformat(row["value"])
    assert stamp.tzinfo is not None  # stored as aware UTC
