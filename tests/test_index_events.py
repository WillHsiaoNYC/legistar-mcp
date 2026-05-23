from legistar_mcp.db import init_db
from legistar_mcp.index.build import index_event_file


def test_index_event_inserts_row(tmp_path, events_dir):
    conn = init_db(tmp_path / "t.db")
    p = next(events_dir.glob("*.json"))
    index_event_file(conn, p, archive_root=events_dir.parent)
    conn.commit()
    row = conn.execute("SELECT id, body_name, date FROM events").fetchone()
    assert row["body_name"]
    assert row["date"]


def test_index_event_writes_one_fts_row_per_item(tmp_path, events_dir):
    conn = init_db(tmp_path / "t.db")
    p = next(events_dir.glob("*.json"))
    index_event_file(conn, p, archive_root=events_dir.parent)
    conn.commit()
    n_items = conn.execute(
        "SELECT COUNT(*) FROM events_fts_map WHERE event_id = (SELECT id FROM events LIMIT 1)"
    ).fetchone()[0]
    assert n_items >= 1
