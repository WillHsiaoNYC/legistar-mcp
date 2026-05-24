from pathlib import Path
from legistar_mcp.agency import load_agencies, resolve_to_fts_query

# agencies.yaml ships inside the package so `uv tool install` carries it
# along. Tests still read it via a Path for the load_agencies surface.
YAML_PATH = Path(__file__).parent.parent / "src" / "legistar_mcp" / "agencies.yaml"


def test_load_returns_canonical_dict():
    a = load_agencies(YAML_PATH)
    assert "mayors-office-of-operations" in a
    assert "Office of Operations" in a["mayors-office-of-operations"]["aliases"]


def test_resolve_by_display_name():
    a = load_agencies(YAML_PATH)
    q = resolve_to_fts_query("Mayor's Office of Operations", a)
    assert "office of operations" in q.lower()


def test_resolve_by_short_alias():
    a = load_agencies(YAML_PATH)
    q = resolve_to_fts_query("NYCHA", a)
    assert "nycha" in q.lower()
    assert "housing authority" in q.lower()


def test_unknown_agency_falls_back_to_phrase_query():
    a = load_agencies(YAML_PATH)
    q = resolve_to_fts_query("Department of Made-Up Things", a)
    assert '"department of made-up things"' in q.lower()


def test_dcwp_resolves_via_current_and_historical_names():
    # DCWP was renamed from "Department of Consumer Affairs" (DCA) in 2020.
    # Both names must resolve to the same canonical agency so that queries
    # over the full bill archive (pre- and post-2020) work uniformly.
    a = load_agencies(YAML_PATH)
    canonical = "department of consumer and worker protection"

    q_new = resolve_to_fts_query("DCWP", a)
    assert canonical in q_new.lower()

    q_old = resolve_to_fts_query("Department of Consumer Affairs", a)
    assert canonical in q_old.lower()
