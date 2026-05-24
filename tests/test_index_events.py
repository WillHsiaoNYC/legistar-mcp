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


def test_index_event_populates_event_items_for_matter(tmp_path, fixtures_root):
    """Items with MatterID get mirrored into event_items; items without are skipped."""
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)

    import json
    fixture_event = fixtures_root / "events" / "2024_city-council.json"
    with open(fixture_event, encoding="utf-8") as f:
        evt = json.load(f)
    expected = sum(1 for item in (evt.get("Items") or []) if item.get("MatterID"))
    assert expected >= 3  # guard against future fixture edits that drop coverage

    actual = conn.execute(
        "SELECT COUNT(*) FROM event_items WHERE event_id = ?", (evt["ID"],)
    ).fetchone()[0]
    assert actual == expected


def test_index_event_skips_items_without_matter_id(tmp_path, fixtures_root):
    """Procedural items (no MatterID) don't appear in event_items, and existing
    rows all carry the source MatterID as bill_id."""
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    rows = conn.execute("SELECT bill_id FROM event_items").fetchall()
    assert rows  # not vacuous — we have at least one bill-bearing item
    assert all(r["bill_id"] is not None for r in rows)


def test_index_event_preserves_action_name_when_present(tmp_path, fixtures_root):
    """ActionName flows into event_items.action_name; absent ActionName becomes NULL."""
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    # Fixture event item 411099 has ActionName "Hearing Held by Committee".
    # Item 411101 omits ActionName entirely.
    row1 = conn.execute(
        "SELECT action_name FROM event_items WHERE item_id = 411099"
    ).fetchone()
    assert row1["action_name"] == "Hearing Held by Committee"
    row2 = conn.execute(
        "SELECT action_name FROM event_items WHERE item_id = 411101"
    ).fetchone()
    assert row2["action_name"] is None
