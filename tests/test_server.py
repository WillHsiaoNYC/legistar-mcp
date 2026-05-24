import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all


def test_make_server_constructs(tmp_path, fixtures_root, monkeypatch):
    # Set up a minimal indexed DB. build_all writes archive_root into the
    # index_state table, which is now the canonical source for the server.
    db_path = tmp_path / "t.db"
    conn = init_db(db_path)
    build_all(conn, archive_root=fixtures_root)
    conn.close()

    monkeypatch.setenv("LEGISTAR_DB_PATH", str(db_path))
    monkeypatch.delenv("LEGISTAR_ARCHIVE_PATH", raising=False)

    from legistar_mcp.server import make_server

    server = make_server()
    assert server is not None


def test_make_server_fails_fast_when_db_env_missing(monkeypatch):
    monkeypatch.delenv("LEGISTAR_DB_PATH", raising=False)
    monkeypatch.delenv("LEGISTAR_ARCHIVE_PATH", raising=False)

    from legistar_mcp.server import make_server

    with pytest.raises(Exception):
        make_server()


def test_server_module_exposes_db_lock():
    """Tools must serialize on `_db_lock` to be safe under any future
    multi-threaded FastMCP transport. Catches accidental removal of the lock
    object (a silent regression that would only surface under load).
    """
    import threading

    from legistar_mcp import server as srv

    assert isinstance(srv._db_lock, type(threading.Lock()))


def test_make_server_fails_fast_when_db_lacks_archive_root(tmp_path, monkeypatch):
    # DB exists but was never indexed → index_state empty → server must refuse.
    db_path = tmp_path / "empty.db"
    init_db(db_path).close()

    monkeypatch.setenv("LEGISTAR_DB_PATH", str(db_path))
    monkeypatch.delenv("LEGISTAR_ARCHIVE_PATH", raising=False)

    from legistar_mcp.server import make_server

    with pytest.raises(Exception, match="archive_root"):
        make_server()
