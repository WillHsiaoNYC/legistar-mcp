import json
from pathlib import Path
from sqlite3 import Connection


def index_bill_file(conn: Connection, json_path: Path, archive_root: Path) -> None:
    with open(json_path, encoding="utf-8") as f:
        b = json.load(f)

    rel_path = json_path.resolve().relative_to(archive_root.resolve()).as_posix()

    conn.execute(
        """INSERT OR REPLACE INTO bills
           (id, file, name, title, summary, type_name, status_name,
            body_id, body_name, intro_date, enactment_date, last_modified, path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            b["ID"], b["File"], b.get("Name"), b.get("Title"), b.get("Summary"),
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

    # Sponsors: replace whole set on reindex
    conn.execute("DELETE FROM sponsors WHERE bill_id = ?", (b["ID"],))
    for i, s in enumerate(b.get("Sponsors") or []):
        slug = s.get("Slug")
        if slug:
            conn.execute(
                "INSERT OR IGNORE INTO sponsors (bill_id, person_slug, sequence) VALUES (?, ?, ?)",
                (b["ID"], slug, i),
            )


def index_event_file(conn: Connection, json_path: Path, archive_root: Path) -> None:
    with open(json_path, encoding="utf-8") as f:
        e = json.load(f)

    rel_path = json_path.resolve().relative_to(archive_root.resolve()).as_posix()
    conn.execute(
        """INSERT OR REPLACE INTO events
           (id, body_id, body_name, date, location, last_modified, path)
           VALUES (?,?,?,?,?,?,?)""",
        (
            e["ID"], e.get("BodyID"), e.get("BodyName"),
            e.get("Date"), e.get("Location"),
            e.get("LastModified"), rel_path,
        ),
    )

    # Clear and reinsert per-item FTS rows
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
