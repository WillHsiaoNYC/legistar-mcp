import html
import re
from pathlib import Path
from sqlite3 import Connection

from ..agency import load_agencies

_AGENCIES_PATH = Path(__file__).parent.parent.parent.parent / "agencies.yaml"
_agencies_cache: dict | None = None


def _get_agencies() -> dict:
    global _agencies_cache
    if _agencies_cache is None:
        _agencies_cache = load_agencies(_AGENCIES_PATH)
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
    lo = text.lower()
    for phrase in phrases:
        idx = lo.find(phrase.lower())
        if idx >= 0:
            start = max(0, idx - window)
            end = min(len(text), idx + len(phrase) + window)
            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            # Escape segments before wrapping so source text containing `<`/`>`
            # doesn't corrupt rendering in HTML/Markdown-aware MCP clients.
            head = html.escape(text[start:idx])
            match = html.escape(text[idx : idx + len(phrase)])
            tail = html.escape(text[idx + len(phrase) : end])
            return f"{prefix}{head}<mark>{match}</mark>{tail}{suffix}"
    return None
