import json
from pathlib import Path
from sqlite3 import Connection


def index_bill_file(conn: Connection, json_path: Path, archive_root: Path) -> None:
    with open(json_path, encoding="utf-8") as f:
        b = json.load(f)

    rel_path = str(json_path.resolve().relative_to(archive_root.resolve()))

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
