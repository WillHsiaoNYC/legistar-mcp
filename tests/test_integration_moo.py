import os
from pathlib import Path

import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import search_bills

ARCHIVE = os.environ.get(
    "LEGISTAR_ARCHIVE_PATH",
    str(Path(__file__).parent.parent.parent / "nyc_legislation"),
)


@pytest.mark.slow
def test_moo_role_context_from_real_archive(tmp_path):
    archive = Path(ARCHIVE)
    if not archive.exists():
        pytest.skip(f"archive not present at {archive}")

    conn = init_db(tmp_path / "real.db")
    stats = build_all(conn, archive_root=archive)
    # Sanity: full archive walk should produce ~19,672 bills + ~16,776 events + 247 people.
    assert stats["bills"] >= 19_000, f"too few bills indexed: {stats}"
    assert stats["events"] >= 16_000
    assert stats["people"] >= 200

    # NOTE: plan suggested limit=20, but search_bills returns newest-first and the
    # real archive has so many MOO-mentioning bills in 2024-2025 that 20 slots
    # don't reach the 2022 witness. Use a wider window (still scoped to a single
    # agency since 2022) so the gate actually exercises the witness bill.
    results = search_bills(
        conn,
        agency="Mayor's Office of Operations",
        year_from=2022,
        limit=200,
    )
    assert results, "expected at least one bill mentioning MOO since 2022"

    # The known witness must appear in the results.
    witness = next((r for r in results if r["file"] == "Int 0153-2022"), None)
    assert witness is not None, (
        "expected Int 0153-2022 in MOO results; "
        f"got {len(results)} bills, first 10: "
        + ", ".join(r["file"] for r in results[:10])
    )

    # And it must have role-context mentions.
    assert witness["mentions"], "MOO bill must include role snippets"
    joined = " ".join(m["snippet"].lower() for m in witness["mentions"])
    assert "office of operations" in joined
    # Some role-indicating word should be in there.
    assert any(
        w in joined for w in ("consultation", "report", "submit", "established", "shall")
    ), f"no role-indicating word in mentions: {joined!r}"
