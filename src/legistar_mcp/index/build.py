import json
from pathlib import Path
from sqlite3 import Connection


def index_bill_file(conn: Connection, json_path: Path, archive_root: Path) -> None:
    with open(json_path, encoding="utf-8") as f:
        b = json.load(f)

    rel_path = json_path.resolve().relative_to(archive_root.resolve()).as_posix()

    conn.execute(
        """INSERT OR REPLACE INTO bills
           (id, guid, file, name, title, summary, type_name, status_name,
            body_id, body_name, intro_date, enactment_date, last_modified, path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            b["ID"], b.get("GUID"), b["File"], b.get("Name"), b.get("Title"), b.get("Summary"),
            b.get("TypeName"), b.get("StatusName"),
            b.get("BodyID"), b.get("BodyName"),
            b.get("IntroDate"), b.get("EnactmentDate"),
            b.get("LastModified"), rel_path,
        ),
    )

    # Contentless FTS5 — we own fts_rowid via bills_fts_map.
    existing = conn.execute(
        "SELECT fts_rowid FROM bills_fts_map WHERE bill_id = ?", (b["ID"],)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM bills_fts WHERE rowid = ?", (existing["fts_rowid"],))
        fts_rowid = existing["fts_rowid"]
    else:
        fts_rowid = (
            conn.execute("SELECT COALESCE(MAX(fts_rowid), 0) + 1 FROM bills_fts_map").fetchone()[0]
        )
        conn.execute(
            "INSERT INTO bills_fts_map (bill_id, fts_rowid) VALUES (?, ?)",
            (b["ID"], fts_rowid),
        )
    conn.execute(
        "INSERT INTO bills_fts (rowid, name, title, summary, text) VALUES (?, ?, ?, ?, ?)",
        (fts_rowid, b.get("Name") or "", b.get("Title") or "",
         b.get("Summary") or "", b.get("Text") or ""),
    )

    conn.execute("DELETE FROM sponsors WHERE bill_id = ?", (b["ID"],))
    for i, s in enumerate(b.get("Sponsors") or []):
        slug = s.get("Slug")
        if slug:
            conn.execute(
                "INSERT OR IGNORE INTO sponsors (bill_id, person_slug, sequence) VALUES (?, ?, ?)",
                (b["ID"], slug, i),
            )

    # Mirror History[].Votes[] into the votes table for voting-record queries.
    # Clear stale vote rows for this bill (handles reindex of an amended bill).
    conn.execute("DELETE FROM votes WHERE bill_id = ?", (b["ID"],))

    for hist in b.get("History") or []:
        record_id = hist.get("ID")
        if record_id is None:
            continue
        event_id = hist.get("EventID")
        date = hist.get("Date")
        action = hist.get("Action")
        passed = hist.get("PassedFlag")
        for vote in hist.get("Votes") or []:
            slug = vote.get("Slug")
            if not slug:
                continue  # no person mapping — skip
            value = vote.get("Vote")
            if not value:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO votes "
                "(history_record_id, person_slug, bill_id, event_id, "
                " vote_value, vote_date, action, passed_flag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (record_id, slug, b["ID"], event_id, value, date, action, passed),
            )


def index_event_file(conn: Connection, json_path: Path, archive_root: Path) -> None:
    with open(json_path, encoding="utf-8") as f:
        e = json.load(f)

    rel_path = json_path.resolve().relative_to(archive_root.resolve()).as_posix()
    # Clear stale event_items rows so reindexing a single event doesn't
    # accumulate orphaned mirrors (Items[] can shrink between snapshots).
    conn.execute("DELETE FROM event_items WHERE event_id = ?", (e["ID"],))
    conn.execute(
        """INSERT OR REPLACE INTO events
           (id, guid, body_id, body_name, date, location, last_modified, path)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            e["ID"], e.get("GUID"), e.get("BodyID"), e.get("BodyName"),
            e.get("Date"), e.get("Location"),
            e.get("LastModified"), rel_path,
        ),
    )

    old = conn.execute(
        "SELECT fts_rowid FROM events_fts_map WHERE event_id = ?", (e["ID"],)
    ).fetchall()
    for r in old:
        conn.execute("DELETE FROM events_fts WHERE rowid = ?", (r["fts_rowid"],))
    conn.execute("DELETE FROM events_fts_map WHERE event_id = ?", (e["ID"],))

    next_rowid = (
        conn.execute("SELECT COALESCE(MAX(fts_rowid), 0) FROM events_fts_map").fetchone()[0] + 1
    )
    for item in e.get("Items") or []:
        seq = item.get("AgendaSequence") or item.get("MinutesSequence") or 0
        conn.execute(
            "INSERT INTO events_fts_map (fts_rowid, event_id, item_sequence) VALUES (?, ?, ?)",
            (next_rowid, e["ID"], seq),
        )
        conn.execute(
            "INSERT INTO events_fts (rowid, item_title, agenda_note, minutes_note) VALUES (?, ?, ?, ?)",
            (next_rowid, item.get("Title") or "", item.get("AgendaNote") or "",
             item.get("MinutesNote") or ""),
        )
        next_rowid += 1

        # Mirror to event_items for bill <-> event linkage queries.
        if item.get("MatterID"):
            item_id = item.get("ID")
            if item_id is None:
                # Skip malformed items; bill linkage requires globally unique
                # ID for the event_items PK. Without it, INSERT would fail
                # mid-transaction and abort the whole event's indexing.
                continue
            conn.execute(
                "INSERT OR REPLACE INTO event_items "
                "(item_id, event_id, bill_id, item_title, item_sequence, action_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    e["ID"],
                    item["MatterID"],
                    item.get("Title"),
                    seq,
                    item.get("ActionName"),
                ),
            )


def index_person_file(conn: Connection, json_path: Path, archive_root: Path) -> None:
    with open(json_path, encoding="utf-8") as f:
        p = json.load(f)
    rel_path = json_path.resolve().relative_to(archive_root.resolve()).as_posix()
    conn.execute(
        """INSERT OR REPLACE INTO people
           (slug, id, full_name, is_active, start_date, end_date, path)
           VALUES (?,?,?,?,?,?,?)""",
        (
            p.get("Slug"), p.get("ID"), p.get("FullName"),
            1 if p.get("IsActive") else 0,
            p.get("Start"), p.get("End"), rel_path,
        ),
    )
