from pathlib import Path
from legistar_mcp.agency import load_agencies, resolve_to_fts_query

YAML_PATH = Path(__file__).parent.parent / "agencies.yaml"


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
