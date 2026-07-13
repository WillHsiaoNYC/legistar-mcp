import html
import re
from importlib.resources import files
from pathlib import Path
from sqlite3 import Connection

import yaml

_agencies_cache: dict | None = None


def _get_agencies() -> dict:
    """Load and cache agencies.yaml from package data.

    Uses importlib.resources so this works after `uv tool install` (where
    the package lives under site-packages and a __file__-walk wouldn't find
    a sibling YAML).
    """
    global _agencies_cache
    if _agencies_cache is None:
        text = files("legistar_mcp").joinpath("agencies.yaml").read_text(encoding="utf-8")
        _agencies_cache = yaml.safe_load(text) or {}
    return _agencies_cache


def _archive_root(conn: Connection) -> Path | None:
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'archive_root'"
    ).fetchone()
    return Path(row["value"]) if row else None


def _extract_phrases(fts_query: str) -> list[str]:
    # Pulls each "quoted phrase" out of a resolved FTS query like
    #   "Mayor's Office of Operations" OR "Office of Operations"
    return re.findall(r'"([^"]+)"', fts_query)


def _build_snippet(
    text: str, phrases: list[str], window: int = 120
) -> str | None:
    # re.IGNORECASE keeps match offsets in the ORIGINAL string. Computing
    # offsets on text.lower() shifted them whenever lowercasing changes
    # length (e.g. 'İ' → 'i̇'), corrupting the <mark> placement.
    for phrase in phrases:
        m = re.search(re.escape(phrase), text, re.IGNORECASE)
        if m:
            idx, match_end = m.start(), m.end()
            start = max(0, idx - window)
            end = min(len(text), match_end + window)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            # Escape segments before wrapping so source text containing `<`/`>`
            # doesn't corrupt rendering in HTML/Markdown-aware MCP clients.
            head = html.escape(text[start:idx])
            match = html.escape(text[idx:match_end])
            tail = html.escape(text[match_end:end])
            return f"{prefix}{head}<mark>{match}</mark>{tail}{suffix}"
    return None
