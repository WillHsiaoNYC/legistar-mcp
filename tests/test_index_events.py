import pytest

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


def test_index_event_writes_one_fts_row_per_item(tmp_path, fixtures_root):
    """Every Items[] entry should produce exactly one events_fts_map row.

    Computes expected from the fixture itself so the assertion stays tight as
    fixtures grow — a regression that drops 1 of 4 items still fails.
    """
    import json

    conn = init_db(tmp_path / "t.db")
    fixture_event = fixtures_root / "events" / "2024_city-council.json"
    index_event_file(conn, fixture_event, archive_root=fixtures_root)
    conn.commit()
    with open(fixture_event, encoding="utf-8") as f:
        evt = json.load(f)
    expected_items = len(evt.get("Items") or [])
    assert expected_items >= 2, "fixture must have multiple items to exercise the index"
    n_items = conn.execute(
        "SELECT COUNT(*) FROM events_fts_map WHERE event_id = ?", (evt["ID"],)
    ).fetchone()[0]
    assert n_items == expected_items


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


def test_index_event_rolls_back_on_mid_indexing_error(tmp_path):
    """If a downstream INSERT raises mid-file, the events row must not
    survive. The per-file `with conn:` wrapper guarantees rollback.
    """
    import json
    from legistar_mcp.db import init_db
    from legistar_mcp.index.build import index_event_file

    conn = init_db(tmp_path / "t.db")
    # Synthetic event: legitimately indexed, then we'll force a failure
    # by smuggling an Items[] entry whose MatterID is a string of more
    # than the event_items.bill_id TEXT/INTEGER affinity could accept...
    # Easier route: monkeypatch conn.execute to raise on a specific call.
    event = {
        "ID": 77777,
        "GUID": "EVT-GUID",
        "BodyID": 1,
        "BodyName": "Committee Z",
        "Date": "2024-08-15T13:30:00-04:00",
        "Location": "Loc",
        "LastModified": "2024-08-10T00:00:00Z",
        "Items": [
            {
                "ID": 800001,
                "MatterID": 1234,
                "Title": "good item",
                "AgendaSequence": 1,
            },
        ],
    }
    archive_root = tmp_path / "archive"
    events_dir = archive_root / "events"
    events_dir.mkdir(parents=True)
    event_path = events_dir / "synthetic.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    # Wrap conn in a proxy that raises on the event_items insert. By the time
    # that fires we've already inserted the events row, FTS rows, and one
    # event_items row — a perfect rollback target.
    class FlakyConn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *args, **kwargs):
            if "INSERT OR REPLACE INTO event_items" in sql:
                raise RuntimeError("simulated mid-file failure")
            return self._real.execute(sql, *args, **kwargs)

        def __enter__(self):
            return self._real.__enter__()

        def __exit__(self, *exc):
            return self._real.__exit__(*exc)

    flaky = FlakyConn(conn)
    with pytest.raises(RuntimeError, match="simulated"):
        index_event_file(flaky, event_path, archive_root=archive_root)

    # The event row should NOT exist; the partial write was rolled back.
    row = conn.execute("SELECT id FROM events WHERE id = 77777").fetchone()
    assert row is None, "events row leaked despite rollback"
    fts_rows = conn.execute(
        "SELECT 1 FROM events_fts_map WHERE event_id = 77777"
    ).fetchall()
    assert not fts_rows, "events_fts_map row leaked despite rollback"


def test_index_event_skips_item_with_matter_id_but_missing_id(tmp_path):
    """An item with MatterID but no ID must be skipped, not abort the
    whole event's indexing with a KeyError."""
    import json
    from legistar_mcp.db import init_db
    from legistar_mcp.index.build import index_event_file

    conn = init_db(tmp_path / "t.db")
    event = {
        "ID": 88888,
        "GUID": "EVT-GUID",
        "BodyID": 1,
        "BodyName": "Committee Z",
        "Date": "2024-08-15T13:30:00-04:00",
        "Location": "Loc",
        "LastModified": "2024-08-10T00:00:00Z",
        "Items": [
            # First item: valid — MatterID and ID both present.
            {
                "ID": 700001,
                "MatterID": 1234,
                "Title": "good item",
                "ActionName": "Hearing Held by Committee",
                "AgendaSequence": 1,
            },
            # Second item: malformed — MatterID present but ID missing.
            {
                "MatterID": 5678,
                "Title": "malformed item with no ID",
                "AgendaSequence": 2,
            },
            # Third item: valid — should still get indexed after the skip.
            {
                "ID": 700003,
                "MatterID": 9012,
                "Title": "another good item",
                "AgendaSequence": 3,
            },
        ],
    }
    archive_root = tmp_path / "archive"
    events_dir = archive_root / "events"
    events_dir.mkdir(parents=True)
    event_path = events_dir / "synthetic.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    # Must not raise.
    index_event_file(conn, event_path, archive_root=archive_root)
    conn.commit()

    rows = conn.execute(
        "SELECT item_id, bill_id FROM event_items WHERE event_id = 88888"
    ).fetchall()
    item_ids = {r["item_id"] for r in rows}
    assert item_ids == {700001, 700003}
    # The malformed item was skipped — its MatterID never landed.
    bill_ids = {r["bill_id"] for r in rows}
    assert 5678 not in bill_ids


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
