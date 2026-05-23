from legistar_mcp.db import init_db
from legistar_mcp.index.build import index_person_file


def test_index_person_row(tmp_path, people_dir):
    conn = init_db(tmp_path / "t.db")
    p = next(people_dir.glob("*.json"))
    index_person_file(conn, p, archive_root=people_dir.parent)
    conn.commit()
    row = conn.execute("SELECT slug, full_name FROM people").fetchone()
    assert row["slug"]
    assert row["full_name"]
