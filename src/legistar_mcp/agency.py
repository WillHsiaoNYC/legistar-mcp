from pathlib import Path

import yaml


def load_agencies(yaml_path: Path) -> dict:
    with open(yaml_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _quote(s: str) -> str:
    # FTS5: wrap in double quotes; escape internal double quotes by doubling them.
    return '"' + s.replace('"', '""') + '"'


def resolve_to_fts_query(user_input: str, agencies: dict) -> str:
    """Return an FTS5 query string.

    Matches user_input against each agency's display name + aliases (case-insensitive).
    On hit, returns an OR'd phrase query over all aliases — so any phrasing in a bill
    can match. Unknown agencies fall back to a phrase query on the input itself.
    """
    ui = user_input.strip().lower()
    for entry in agencies.values():
        candidates = {entry["display"].lower(), *(a.lower() for a in entry.get("aliases", []))}
        if ui in candidates:
            return " OR ".join(_quote(a) for a in entry.get("aliases", [entry["display"]]))
    return _quote(user_input)
