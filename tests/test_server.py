import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all


def test_make_server_constructs(tmp_path, fixtures_root, monkeypatch):
    # Set up a minimal indexed DB.
    db_path = tmp_path / "t.db"
    conn = init_db(db_path)
    build_all(conn, archive_root=fixtures_root)
    conn.close()

    monkeypatch.setenv("LEGISTAR_DB_PATH", str(db_path))
    monkeypatch.setenv("LEGISTAR_ARCHIVE_PATH", str(fixtures_root))

    # If the server's bootstrap helper exists, it should construct without
    # raising. We don't try to drive the MCP framing protocol here — that's
    # tested by Claude Desktop integration (Task 24, manual).
    from legistar_mcp.server import make_server

    server = make_server()
    assert server is not None


def test_make_server_fails_fast_when_env_missing(monkeypatch):
    monkeypatch.delenv("LEGISTAR_DB_PATH", raising=False)
    monkeypatch.delenv("LEGISTAR_ARCHIVE_PATH", raising=False)

    from legistar_mcp.server import make_server

    with pytest.raises(Exception):
        make_server()
