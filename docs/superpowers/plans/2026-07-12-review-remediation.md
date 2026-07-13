# Review Remediation & Agent Ergonomics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 15 verified defects from the 2026-07-12 max-effort code review and make the MCP tool surface agent-proof (validated inputs, guided errors, result envelopes with totals, discovery tools for agencies/data freshness/bill text).

**Architecture:** Four milestone branches, each landing as its own PR: (1) install/first-run blockers in the indexer+db layer, (2) a shared validation module (`tools/_validate.py`) wired through every query tool, (3) indexer integrity (purge, LastModified, schema v5), (4) agent ergonomics (envelope outputs, parameter descriptions, server instructions, three new tools). No schema redesign; all changes are additive to the existing SQLite/FTS5 design.

**Tech Stack:** Python 3.11+, `mcp` (FastMCP, stdio), SQLite FTS5 (contentless), pytest + freezegun, uv, ruff.

## Global Constraints

- Python `>=3.11`; run everything via `uv run …` from the repo root `/Users/willhsiao/Desktop/Air Repo/Legistar-MCP`.
- Tests: `uv run pytest` (pytest config auto-excludes `-m slow`). Lint: `uv run ruff check src tests`. Both must pass before every commit.
- ruff line-length is 100 (`pyproject.toml`).
- The server is read-only: no tool may write to the DB or archive. Tools return JSON-serializable values only.
- Keep the `@server.tool()` + `@_db_locked` decorator pattern in `server.py` — every tool body must stay serialized on the shared connection.
- Commit style follows repo history: `fix(scope): …` / `feat(scope): …` / `chore: …`, one commit per task.
- Branch per milestone (names given per milestone). Create each branch from up-to-date `main`. Do not merge to `main` in this plan — PR creation happens after each milestone via the user's normal flow.
- **Subagent hygiene (user rule):** every subagent prompt must include absolute file paths and must run `pwd` + `git rev-parse --abbrev-ref HEAD` before any write/commit, aborting if the branch is not the expected one.
- Existing public behavior that is NOT flagged in this plan must not change (e.g., sort orders, `legistar_url` construction, snippet `<mark>` format).

---

## Milestone 1 — Install / first-run blockers

**Branch:** `fix/install-blockers` (from `main`).

Review findings addressed: #1 (fresh-DB incremental refusal), #2 (SQLite floor 3.30 vs contentless_delete needing 3.43), #3 (zero-file archive "success"), plus README/docstring drift.

### Task 1: Fresh DB auto-promotes incremental → full

**Files:**
- Modify: `src/legistar_mcp/index/bulk.py:47-67` (`build_all`)
- Test: `tests/test_index_bulk.py`

**Interfaces:**
- Produces: `build_all(conn, archive_root, incremental=True)` succeeds on a freshly `init_db`'d DB, indexes everything, and stamps `PRAGMA user_version = SCHEMA_VERSION`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_bulk.py`:

```python
def test_incremental_on_fresh_db_succeeds_and_stamps_version(tmp_path, fixtures_root):
    """README quickstart uses the CLI default (--incremental) on a brand-new DB.
    A fresh DB must auto-promote to a full build instead of raising."""
    from legistar_mcp.db import SCHEMA_VERSION, init_db
    conn = init_db(tmp_path / "fresh.db")
    stats = build_all(conn, archive_root=fixtures_root, incremental=True)
    assert stats["bills"] > 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_incremental_on_stale_populated_db_still_refused(tmp_path, fixtures_root):
    """The guard must still protect populated-but-stale DBs."""
    from legistar_mcp.db import init_db
    import pytest
    conn = init_db(tmp_path / "stale.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    conn.execute("PRAGMA user_version = 1")  # simulate data indexed by an old release
    conn.commit()
    with pytest.raises(RuntimeError, match="Re-run with --full"):
        build_all(conn, archive_root=fixtures_root, incremental=True)
```

Check the top of `tests/test_index_bulk.py` first: it already imports `build_all`; reuse its existing imports rather than duplicating.

- [ ] **Step 2: Run tests to verify the first fails**

Run: `uv run pytest tests/test_index_bulk.py -v`
Expected: `test_incremental_on_fresh_db_succeeds_and_stamps_version` FAILS with `RuntimeError: Incremental reindex refused…`; the stale-DB test passes (guard already exists).

- [ ] **Step 3: Implement the auto-promotion**

In `src/legistar_mcp/index/bulk.py`, at the very top of `build_all` (before the existing stale-schema guard), insert:

```python
    # A brand-new DB (no bills rows) has user_version=0, which the stale-schema
    # guard below would refuse even though there is nothing stale — the CLI
    # default (--incremental) would then fail on first run. Incremental is
    # meaningless with no prior rows anyway, so promote to a full build; that
    # also stamps user_version at the end.
    has_rows = conn.execute("SELECT 1 FROM bills LIMIT 1").fetchone() is not None
    if incremental and not has_rows:
        incremental = False
```

Leave the existing `if incremental and current_version < SCHEMA_VERSION:` guard immediately after, unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_bulk.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite and commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/index/bulk.py tests/test_index_bulk.py
git commit -m "fix(index): fresh DB auto-promotes incremental to full so first run succeeds"
```

### Task 2: Raise the SQLite floor to 3.43 (contentless_delete)

**Files:**
- Modify: `src/legistar_mcp/db.py:24-34` (`open_db` version gate)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `open_db` raises `RuntimeError` mentioning `3.43` and `contentless_delete` when `sqlite3.sqlite_version_info < (3, 43)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_open_db_requires_sqlite_343(tmp_path, monkeypatch):
    """schema.sql uses FTS5 contentless_delete=1, introduced in SQLite 3.43.
    Older libraries pass a 3.30 gate and then crash on CREATE VIRTUAL TABLE
    with a cryptic error — the gate must catch them with a clear message."""
    import legistar_mcp.db as db_mod
    import pytest
    monkeypatch.setattr(db_mod.sqlite3, "sqlite_version_info", (3, 37, 0))
    monkeypatch.setattr(db_mod.sqlite3, "sqlite_version", "3.37.0")
    with pytest.raises(RuntimeError, match="3.43"):
        db_mod.open_db(tmp_path / "x.db")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_open_db_requires_sqlite_343 -v`
Expected: FAIL — no exception raised (3.37 passes the current 3.30 gate).

- [ ] **Step 3: Update the gate**

In `src/legistar_mcp/db.py`, replace the version-gate block at the top of `open_db`:

```python
    # schema.sql's FTS5 tables use contentless_delete=1, added in SQLite 3.43
    # (Aug 2023). Older libraries fail CREATE VIRTUAL TABLE with an opaque
    # "unrecognized option" error, and the indexer's DELETE FROM *_fts is
    # illegal on contentless tables without it. (3.43 also covers the NULLS
    # LAST ordering vote_breakdown needs, added in 3.30.) Modern CPython and
    # uv-managed interpreters bundle 3.45+; Linux system Pythons may not.
    if sqlite3.sqlite_version_info < (3, 43):
        raise RuntimeError(
            f"SQLite >= 3.43 required (FTS5 contentless_delete). Found "
            f"{sqlite3.sqlite_version}. Use a Python whose sqlite3 links a "
            f"newer libsqlite3 (e.g. a uv-managed interpreter: uv python install)."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: all PASS (the running interpreter's own SQLite is ≥3.43, so other tests are unaffected).

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/db.py tests/test_db.py
git commit -m "fix(db): require SQLite >= 3.43 — contentless_delete floor, not 3.30"
```

### Task 3: Error on an archive directory with no content

**Files:**
- Modify: `src/legistar_mcp/index/bulk.py:69-90` (`build_all` — guard + move the `archive_root` persist)
- Test: `tests/test_index_bulk.py`

**Interfaces:**
- Produces: `build_all` raises `RuntimeError` naming the path when the walk finds zero bills, events, AND people; `index_state.archive_root` is NOT persisted and `user_version` is NOT bumped in that case.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_bulk.py`:

```python
def test_empty_archive_dir_errors_instead_of_silent_success(tmp_path):
    """Pointing --archive at an existing-but-wrong directory previously
    'succeeded' with bills=0 and persisted the junk path. It must error."""
    from legistar_mcp.db import init_db
    import pytest
    conn = init_db(tmp_path / "t.db")
    wrong_dir = tmp_path / "not_an_archive"
    wrong_dir.mkdir()
    with pytest.raises(RuntimeError, match="No archive content"):
        build_all(conn, archive_root=wrong_dir, incremental=False)
    # Nothing persisted: no recorded archive_root, version not bumped.
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'archive_root'"
    ).fetchone()
    assert row is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index_bulk.py::test_empty_archive_dir_errors_instead_of_silent_success -v`
Expected: FAIL — no exception; `Indexed: bills=0` path currently succeeds.

- [ ] **Step 3: Implement the guard and reorder the persist**

In `build_all`, the current order is: stale-guard → `INSERT … archive_root` → materialize `bills/events/people` lists. Reorder so materialization happens first, guard second, persist third. Replace the block from the `INSERT OR REPLACE INTO index_state` statement through the three `list(...)` lines with:

```python
    # Materialize the path generators so the progress bars know totals upfront.
    bills = list(_bill_paths(archive_root))
    events = list(_event_paths(archive_root))
    people = list(_person_paths(archive_root))

    # A wrong --archive path (e.g. the parent directory of the real clone)
    # walks zero files and would otherwise "succeed" with bills=0, persist the
    # junk path, and leave every tool returning [] with no diagnostic.
    if not bills and not events and not people:
        raise RuntimeError(
            f"No archive content found under {archive_root}. Expected "
            f"subdirectories like introduction/, resolution/, land_use/, "
            f"events/, people/ (see jehiah/nyc_legislation). Check the "
            f"--archive path."
        )

    # Persist archive_root so query-time tools can resolve relative bills.path
    # back to the source JSON (needed for building snippets server-side, since
    # bills_fts is contentless and SQLite's snippet() returns NULL on it).
    conn.execute(
        "INSERT OR REPLACE INTO index_state (key, value) VALUES ('archive_root', ?)",
        (str(archive_root.resolve()),),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_bulk.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/index/bulk.py tests/test_index_bulk.py
git commit -m "fix(index): refuse to index an archive dir with no recognizable content"
```

### Task 4: Documentation alignment (no code)

**Files:**
- Modify: `README.md` ("What this needs" → Requirements), `src/legistar_mcp/server.py:1` and `:75` (module + `make_server` docstrings)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update README requirements**

In `README.md`, in the `**Requirements:**` list, replace the Python bullet with:

```markdown
- **Python 3.11+** with **SQLite ≥ 3.43** (check: `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`).
  Interpreters installed by [`uv`](https://docs.astral.sh/uv/) bundle a current
  SQLite; Linux *system* Pythons on older distros may not. uv handles the
  install — it's the only Python toolchain you need to know about.
```

- [ ] **Step 2: Remove hardcoded tool counts from server.py docstrings**

`server.py:1` says "17 Legistar tools", `make_server`'s docstring says "all 16 Legistar tools" — one is already wrong, and Milestone 4 adds three more. Change both phrases to "the Legistar tools" (count-free).

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest && uv run ruff check src tests
grep -rn "1[67] Legistar tools" src/ README.md   # expected: no matches
git add README.md src/legistar_mcp/server.py
git commit -m "docs: SQLite 3.43 requirement; drop drifting tool counts"
```

**Milestone 1 checkpoint:** push `fix/install-blockers`, open a PR titled `fix: install/first-run blockers (fresh-DB index, SQLite 3.43 floor, empty-archive guard)`.

---

## Milestone 2 — Query-layer validation & correct answers

**Branch:** `fix/query-validation` (from `main` after PR 1 merges, or stacked on M1 if executing continuously).

Review findings addressed: #4 (query+agency), #5 (year padding), #6 (date validation), #7 (timezone), #8 (aggregate description overclaim), #9 (negative days), #10 (raw FTS5), #11 (unbounded limit), #12 (unguarded archive reads), plus live-verified traps: case-sensitive filters, single-substring people search, snippet Unicode misalignment, plain-query mentions, identifier inconsistency (`vote_breakdown` file param), unknown-identifier guidance.

### Task 5: Shared validation module

**Files:**
- Create: `src/legistar_mcp/tools/_validate.py`
- Test: `tests/test_validate.py` (new)

**Interfaces:**
- Produces (later tasks import these exact names from `..tools._validate` / `._validate`):
  - `clamp_limit(limit: int, hi: int = 200) -> int`
  - `clamp_offset(offset: int) -> int`
  - `validate_year(name: str, value: int | None) -> int | None`
  - `validate_iso_date(name: str, value: str | None) -> str | None`
  - `validate_days(days: int) -> int`
  - `today_nyc() -> datetime.date`
  - `normalize_fts_query(conn, fts_table: str, raw: str) -> str | None`
  - `build_fts_query(conn, table: str, query: str | None, agency: str | None) -> str | None`
  - `envelope(results: list, total: int, offset: int = 0) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_validate.py`:

```python
import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools._validate import (
    build_fts_query,
    clamp_limit,
    clamp_offset,
    envelope,
    normalize_fts_query,
    today_nyc,
    validate_days,
    validate_iso_date,
    validate_year,
)


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_clamp_limit_bounds():
    assert clamp_limit(20) == 20
    assert clamp_limit(-1) == 1      # SQLite LIMIT -1 means unlimited — never pass it through
    assert clamp_limit(0) == 1
    assert clamp_limit(10_000) == 200
    assert clamp_limit(10_000, hi=1000) == 1000


def test_clamp_offset():
    assert clamp_offset(0) == 0
    assert clamp_offset(-5) == 0
    assert clamp_offset(40) == 40


def test_validate_year_range():
    assert validate_year("year_from", None) is None
    assert validate_year("year_from", 2024) == 2024
    with pytest.raises(ValueError, match="year_from"):
        validate_year("year_from", 24)       # 2-digit shorthand silently matched everything before
    with pytest.raises(ValueError, match="year_to"):
        validate_year("year_to", 21024)


def test_validate_iso_date_accepts_prefix_forms():
    assert validate_iso_date("date_from", None) is None
    for ok in ("2024", "2024-08", "2024-08-15", "2024-08-15T13:30:00-04:00"):
        assert validate_iso_date("date_from", ok) == ok
    for bad in ("08/15/2024", "Aug 15 2024", "2024-13", "2024-08-99", "next week"):
        with pytest.raises(ValueError, match="date_from"):
            validate_iso_date("date_from", bad)


def test_validate_days_rejects_nonpositive():
    assert validate_days(7) == 7
    with pytest.raises(ValueError, match="days"):
        validate_days(-7)   # negative inverted the window into the future before
    with pytest.raises(ValueError, match="days"):
        validate_days(0)


def test_today_nyc_returns_a_date():
    import datetime
    assert isinstance(today_nyc(), datetime.date)


def test_normalize_fts_query_passes_valid_and_quotes_invalid(indexed_db):
    # Valid FTS5 stays untouched.
    assert normalize_fts_query(indexed_db, "bills_fts", "housing") == "housing"
    # Punctuated natural language that FTS5 rejects gets phrase-quoted.
    assert normalize_fts_query(indexed_db, "bills_fts", "covid-19") == '"covid-19"'
    assert normalize_fts_query(indexed_db, "bills_fts", "safety (vision zero)") == '"safety (vision zero)"'
    # Whitespace-only means "no text filter".
    assert normalize_fts_query(indexed_db, "bills_fts", "   ") is None
    # Column filters valid on bills_fts but not events_fts get quoted per-table.
    assert normalize_fts_query(indexed_db, "bills_fts", "title:housing") == "title:housing"
    assert normalize_fts_query(indexed_db, "events_fts", "title:housing") == '"title:housing"'


def test_build_fts_query_combines_query_and_agency(indexed_db):
    combined = build_fts_query(indexed_db, "bills", query="housing", agency="NYPD")
    assert combined is not None
    assert "housing" in combined and " AND " in combined and "NYPD" in combined
    assert build_fts_query(indexed_db, "bills", query=None, agency=None) is None
    only_q = build_fts_query(indexed_db, "bills", query="housing", agency=None)
    assert only_q == "(housing)"


def test_envelope_shape():
    e = envelope([1, 2], total=10, offset=2)
    assert e == {"results": [1, 2], "total": 10, "offset": 2, "truncated": True}
    assert envelope([1], total=1)["truncated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate.py -v`
Expected: FAIL at import — `No module named 'legistar_mcp.tools._validate'`.

- [ ] **Step 3: Implement the module**

Create `src/legistar_mcp/tools/_validate.py`:

```python
"""Input validation, FTS-query hygiene, and response envelopes shared by every
MCP tool. Agents routinely send imperfect inputs (negative limits, US-format
dates, punctuated natural-language queries); each helper either coerces the
input to something safe or raises ValueError whose message tells the agent how
to fix the call — never a silent wrong answer.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from sqlite3 import Connection
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..agency import _quote, resolve_to_fts_query
from ._snippet import _get_agencies

_YEAR_LO, _YEAR_HI = 1900, 2100


def clamp_limit(limit: int, hi: int = 200) -> int:
    """SQLite treats a negative LIMIT as unbounded — clamp to [1, hi]."""
    return max(1, min(limit, hi))


def clamp_offset(offset: int) -> int:
    return max(0, offset)


def validate_year(name: str, value: int | None) -> int | None:
    """Years outside a sane window break the lexicographic ISO predicates:
    year_from=24 pads to '0024-01-01' which every real date exceeds (filter
    silently ignored), year_to=24 excludes everything."""
    if value is None:
        return None
    if not _YEAR_LO <= value <= _YEAR_HI:
        raise ValueError(
            f"{name} must be a 4-digit year between {_YEAR_LO} and {_YEAR_HI}, "
            f"got {value!r}. Example: {name}=2024."
        )
    return value


def validate_iso_date(name: str, value: str | None) -> str | None:
    """Accept the ISO prefix forms the date predicates understand: YYYY,
    YYYY-MM, YYYY-MM-DD, or a full ISO timestamp. Anything else (e.g.
    '08/15/2024') would lex-compare meaninglessly against stored ISO dates."""
    if value is None:
        return None
    v = value.strip()
    try:
        if len(v) == 4 and v.isdigit():
            return v
        if len(v) == 7:
            _dt.date.fromisoformat(v + "-01")
            return v
        if len(v) == 10:
            _dt.date.fromisoformat(v)
            return v
        if len(v) > 10:
            _dt.date.fromisoformat(v[:10])
            return v
    except ValueError:
        pass
    raise ValueError(
        f"{name} must be ISO format — YYYY, YYYY-MM, or YYYY-MM-DD "
        f"(e.g. '2024-08-15'), got {value!r}."
    )


def validate_days(days: int) -> int:
    if days < 1:
        raise ValueError(
            f"days must be a positive integer, got {days}. "
            f"Use date-filtered search tools for arbitrary windows."
        )
    return days


def today_nyc() -> _dt.date:
    """The data is NYC government data with -04:00/-05:00 offsets; 'today' for
    windowing must be NYC's calendar day, not the server's (a UTC server after
    ~8pm NYC would otherwise drop tonight's hearings from upcoming_events)."""
    try:
        return _dt.datetime.now(ZoneInfo("America/New_York")).date()
    except ZoneInfoNotFoundError:  # no tz database (bare Windows) — degrade
        return _dt.date.today()


def normalize_fts_query(conn: Connection, fts_table: str, raw: str) -> str | None:
    """Return an FTS5 MATCH string that is guaranteed to parse against
    `fts_table`. Valid syntax passes through (power users keep OR/NEAR/quotes);
    anything FTS5 rejects — 'covid-19', \"don't\", parentheses — is retried as
    one quoted phrase. Whitespace-only means no text filter. Column-filter
    validity differs per table (title: exists on bills_fts, not events_fts),
    hence the live probe instead of a static grammar check."""
    q = raw.strip()
    if not q:
        return None
    try:
        conn.execute(f"SELECT 1 FROM {fts_table} WHERE {fts_table} MATCH ? LIMIT 0", (q,))
        return q
    except sqlite3.OperationalError:
        return _quote(q)


def build_fts_query(
    conn: Connection, table: str, query: str | None, agency: str | None
) -> str | None:
    """Combine the free-text query and the agency alias expansion with AND —
    previously `agency` silently overwrote `query`. `table` is the base table
    name ('bills' or 'events'); the FTS table is f'{table}_fts'."""
    parts: list[str] = []
    if agency:
        parts.append("(" + resolve_to_fts_query(agency, _get_agencies()) + ")")
    if query is not None:
        nq = normalize_fts_query(conn, f"{table}_fts", query)
        if nq:
            parts.append("(" + nq + ")")
    return " AND ".join(parts) if parts else None


def envelope(results: list, total: int, offset: int = 0) -> dict:
    """Uniform list-tool response: agents can tell '20 of 3,412' from
    'exactly 20', and page with offset."""
    return {
        "results": results,
        "total": total,
        "offset": offset,
        "truncated": offset + len(results) < total,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate.py -v`
Expected: all PASS.

- [ ] **Step 5: Add the Windows tzdata dependency and commit**

In `pyproject.toml` `[project] dependencies`, append `"tzdata; sys_platform == 'win32'"` (IANA database for `zoneinfo` on Windows; no-op elsewhere). Then:

```bash
uv sync && uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/tools/_validate.py tests/test_validate.py pyproject.toml uv.lock
git commit -m "feat(tools): shared validation module — clamps, ISO/year/days checks, FTS normalization, envelope"
```

### Task 6: Clamp `limit` (and pre-clamp `offset`) in every tool

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (`search_bills`, `recent_bills`), `src/legistar_mcp/tools/events.py` (`search_events`, `upcoming_events`, `get_bill_hearings`), `src/legistar_mcp/tools/people.py` (`search_people`), `src/legistar_mcp/tools/relationships.py` (`co_sponsors`, `get_voting_record`, `vote_breakdown`), `src/legistar_mcp/tools/_aggregate.py` (`run_aggregate`)
- Test: `tests/test_validate_wiring.py` (new)

**Interfaces:**
- Consumes: `clamp_limit` from Task 5.
- Produces: every tool that takes `limit` clamps it on entry; aggregates clamp with `hi=1000`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_validate_wiring.py`:

```python
"""Wiring tests: validation helpers must actually be applied inside each tool.
Uses the shared fixtures archive (3 bills, 1 event, 1 person)."""
import pytest

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.bills import recent_bills, search_bills
from legistar_mcp.tools.events import search_events, upcoming_events
from legistar_mcp.tools.people import search_people
from legistar_mcp.tools.relationships import vote_breakdown


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_negative_limit_never_returns_everything(indexed_db):
    # Fixture has 3 bills; a passed-through LIMIT -1 would return all 3.
    assert len(search_bills(indexed_db, limit=-1)) == 1
    assert len(search_people(indexed_db, limit=-1)) == 1
    assert len(search_events(indexed_db, limit=-1)) == 1
    assert len(recent_bills(indexed_db, days=36500, limit=-1)) == 1
    assert len(upcoming_events(indexed_db, days=36500, limit=-1)) <= 1
    assert len(vote_breakdown(indexed_db, bill_id=48979, limit=-1)) <= 1
```

Note: `48979` is the bill `ID` inside `tests/fixtures/bills/int_0153_2022.json` — confirm with `python3 -c "import json; print(json.load(open('tests/fixtures/bills/int_0153_2022.json'))['ID'])"` and substitute the printed value if different.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_validate_wiring.py -v`
Expected: FAIL — `search_bills(limit=-1)` returns 3 rows.

- [ ] **Step 3: Wire the clamps**

In each function listed under **Files**, add as the first line of the body (import `from ._validate import clamp_limit` at each module top):

```python
    limit = clamp_limit(limit)
```

Exceptions: in `relationships.py`'s `get_voting_record` and `vote_breakdown`, and in `_aggregate.py`'s `run_aggregate` (before `validate_group_by`), use `limit = clamp_limit(limit, hi=1000)` — vote breakdowns and aggregations legitimately return more rows. In `_aggregate.py` import with `from ._validate import clamp_limit`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate_wiring.py tests/test_tool_search_bills.py -v`
Expected: all PASS (existing `limit=1` / `limit=5` tests unaffected).

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/tools/ tests/test_validate_wiring.py
git commit -m "fix(tools): clamp limit everywhere — negative limit no longer returns whole tables"
```

### Task 7: FTS query normalization + query/agency combination in the four search paths

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (`search_bills`, `aggregate_bills`), `src/legistar_mcp/tools/events.py` (`search_events`, `aggregate_events`)
- Modify: `src/legistar_mcp/server.py` (`aggregate_bills`/`aggregate_events` wrappers gain `query`; both descriptions rewritten truthfully)
- Test: `tests/test_validate_wiring.py`, `tests/test_tool_aggregate_bills.py`

**Interfaces:**
- Consumes: `build_fts_query` from Task 5.
- Produces: `search_bills`, `search_events`, `aggregate_bills`, `aggregate_events` all accept `query` and `agency` together (ANDed); punctuated queries never raise; `aggregate_*` gain a `query: str | None = None` parameter.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate_wiring.py`:

```python
def test_punctuated_query_does_not_crash(indexed_db):
    # These previously raised sqlite3.OperationalError from FTS5.
    for q in ("covid-19", "don't", "safety (vision zero)", "   "):
        search_bills(indexed_db, query=q, limit=5)
        search_events(indexed_db, query=q, limit=5)


def test_query_and_agency_combine_instead_of_override(indexed_db):
    # Fixture Int 0153-2022 mentions the Mayor's Office of Operations.
    # agency alone matches it; adding an unrelated query must NARROW, not override.
    agency_only = search_bills(indexed_db, agency="Mayor's Office of Operations", limit=5)
    assert any("0153-2022" in r["file"] for r in agency_only)
    combined = search_bills(
        indexed_db, query="zzzunfindable", agency="Mayor's Office of Operations", limit=5
    )
    assert combined == []  # query was previously discarded → would return the agency hits
```

Append to `tests/test_tool_aggregate_bills.py` (mirror its existing import/fixture style — read the file header first):

```python
def test_aggregate_bills_accepts_free_text_query(indexed_db):
    from legistar_mcp.tools.bills import aggregate_bills
    rows = aggregate_bills(indexed_db, group_by=["intro_year"], query="domestic violence")
    assert rows, "FTS-filtered aggregate should find the 0153-2022 fixture"
    assert all("intro_year" in r and "count" in r for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate_wiring.py tests/test_tool_aggregate_bills.py -v`
Expected: `test_punctuated_query_does_not_crash` FAILS with `OperationalError`; `test_query_and_agency_combine…` FAILS (returns agency hits); the aggregate test FAILS with `TypeError: unexpected keyword argument 'query'`.

- [ ] **Step 3: Implement**

In `src/legistar_mcp/tools/bills.py`, `search_bills`: replace

```python
    if agency:
        query = resolve_to_fts_query(agency, _get_agencies())
```

with

```python
    fts_query = build_fts_query(conn, "bills", query, agency)
```

and pass `fts_query` (not `query`) to `_bill_filters(...)`. In the snippet block below, replace `phrases = _extract_phrases(query) if query else []` with:

```python
        phrases = _extract_phrases(fts_query or "")
```

(`_extract_phrases` pulls the quoted agency aliases; a raw free-text query contributes phrases in Task 12.) Import `from ._validate import build_fts_query, clamp_limit` and drop the now-unused `resolve_to_fts_query` import if nothing else uses it in the module (`aggregate_bills` will also switch — check before removing).

`aggregate_bills`: add `query: str | None = None` after `group_by`, replace `query = resolve_to_fts_query(agency, _get_agencies()) if agency else None` with `fts_query = build_fts_query(conn, "bills", query, agency)`, pass `fts_query` into `_bill_filters`.

Mirror both changes exactly in `src/legistar_mcp/tools/events.py` (`search_events`, `aggregate_events`) with table `"events"`.

In `src/legistar_mcp/server.py`: add `query: str | None = None` to the `aggregate_bills` and `aggregate_events` wrappers (pass through), and replace the two descriptions:

- `aggregate_bills`: `"""Group bills by one or more dimensions (status_name, type_name, body_name, sponsor_slug, intro_year) and return per-group counts. Filters: query (free text), agency, year_from/year_to, status, type, committee, sponsor_slug."""`
- `aggregate_events`: `"""Group events by one or more dimensions (body_name, event_year, event_month) and return per-group counts. Filters: query (free text), agency, date_from/date_to, committee."""`

Also update the two rows in `README.md`'s Tools table: change "Same filter surface as search_bills/search_events" to the explicit filter lists above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate_wiring.py tests/test_tool_aggregate_bills.py tests/test_tool_search_bills.py tests/test_tool_events.py -v`
Expected: all PASS (existing agency-snippet tests must stay green — `_extract_phrases` still receives the quoted aliases).

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/tools/ src/legistar_mcp/server.py README.md
git commit -m "fix(search): normalize FTS queries, AND query with agency, give aggregates a real query param"
```

### Task 8: Year / date / days validation wiring

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (`search_bills`, `aggregate_bills`, `recent_bills`), `src/legistar_mcp/tools/events.py` (`search_events`, `aggregate_events`, `upcoming_events`), `src/legistar_mcp/tools/committees.py` (`list_committees`), `src/legistar_mcp/tools/relationships.py` (`get_voting_record`), `src/legistar_mcp/tools/_aggregate.py` (`date_upper_bound`)
- Test: `tests/test_validate_wiring.py`, `tests/test_aggregate_helpers.py`

**Interfaces:**
- Consumes: `validate_year`, `validate_iso_date`, `validate_days` from Task 5.
- Produces: out-of-range years, non-ISO dates, and non-positive days raise `ValueError` with corrective guidance in every tool that accepts them; `date_upper_bound` no longer silently accepts malformed input.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate_wiring.py`:

```python
def test_bad_year_and_date_and_days_raise_guidance(indexed_db):
    from legistar_mcp.tools.bills import aggregate_bills
    from legistar_mcp.tools.committees import list_committees
    from legistar_mcp.tools.relationships import get_voting_record

    with pytest.raises(ValueError, match="4-digit year"):
        search_bills(indexed_db, year_from=24)          # previously: filter silently ignored
    with pytest.raises(ValueError, match="4-digit year"):
        aggregate_bills(indexed_db, group_by=["intro_year"], year_to=24)
    with pytest.raises(ValueError, match="4-digit year"):
        list_committees(indexed_db, year_from=99)
    with pytest.raises(ValueError, match="4-digit year"):
        get_voting_record(indexed_db, slug="adrienne-e-adams", year_from=24)
    with pytest.raises(ValueError, match="ISO format"):
        search_events(indexed_db, date_from="08/15/2024")  # previously: matched everything
    with pytest.raises(ValueError, match="ISO format"):
        search_events(indexed_db, date_to="Aug 15")
    with pytest.raises(ValueError, match="days"):
        recent_bills(indexed_db, days=-7)               # previously: future-window inversion
    with pytest.raises(ValueError, match="days"):
        upcoming_events(indexed_db, days=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate_wiring.py::test_bad_year_and_date_and_days_raise_guidance -v`
Expected: FAIL — no ValueError raised anywhere.

- [ ] **Step 3: Wire the validators**

At the top of each affected function body add the relevant lines (imports: `from ._validate import validate_days, validate_iso_date, validate_year` — extend the existing `._validate` import line where one already exists):

- `search_bills`, `aggregate_bills` (bills.py), `list_committees` (committees.py), `get_voting_record` (relationships.py):
  ```python
      year_from = validate_year("year_from", year_from)
      year_to = validate_year("year_to", year_to)
  ```
- `search_events`, `aggregate_events` (events.py):
  ```python
      date_from = validate_iso_date("date_from", date_from)
      date_to = validate_iso_date("date_to", date_to)
  ```
- `recent_bills` (bills.py), `upcoming_events` (events.py):
  ```python
      days = validate_days(days)
  ```

In `src/legistar_mcp/tools/_aggregate.py`, `date_upper_bound`: input is now pre-validated, so the silent fallback is only for full timestamps and the ≥9999 edge. Update its final lines from

```python
    except (ValueError, OverflowError):
        pass
    return f"{col} <= ?", date_to
```

to

```python
    except OverflowError:
        pass  # year 9999: no next-period boundary is representable
    return f"{col} <= ?", date_to
```

and change the `except` on nothing else — the `ValueError` swallow existed to mask malformed input that `validate_iso_date` now rejects upstream. Check `tests/test_aggregate_helpers.py` for direct `date_upper_bound` tests feeding malformed strings; if any exist, update them to assert the documented full-timestamp fallback (`<=`) instead of malformed-string behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate_wiring.py tests/test_aggregate_helpers.py tests/test_tool_committees.py tests/test_tool_voting.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/tools/ tests/
git commit -m "fix(tools): validate years, ISO dates, and days — reject inputs that silently unfiltered"
```

### Task 9: NYC-timezone "today"

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (`recent_bills`), `src/legistar_mcp/tools/events.py` (`upcoming_events`, `get_bill_hearings`)
- Test: `tests/test_validate_wiring.py`

**Interfaces:**
- Consumes: `today_nyc` from Task 5.
- Produces: all three date-window tools compute "today" in `America/New_York`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_validate_wiring.py`:

```python
from freezegun import freeze_time


@freeze_time("2024-04-02 01:00:00")  # 01:00 UTC = 21:00 Apr 1 in NYC
def test_windows_use_nyc_calendar_day(indexed_db):
    """A UTC server just after NYC evening must still treat 'today' as Apr 1.
    recent_bills(days=33) from Apr 1 reaches back to Feb 28 and catches the
    Int 0001-2024 fixture (intro 2024-02-28); from a UTC 'today' of Apr 2 the
    same window starts Feb 29 and misses it."""
    results = recent_bills(indexed_db, days=33, limit=10)
    assert any("0001-2024" in r["file"] for r in results)
```

First confirm the fixture's intro date: `python3 -c "import json; print(json.load(open('tests/fixtures/bills/int_0001_2024.json'))['IntroDate'])"`. If it is not `2024-02-28…`, adjust the frozen instant and `days` so the boundary day is exactly the fixture's intro date in NYC but one day past it in UTC (same construction: freeze at `<intro_date + days + 1 day> 01:00 UTC`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_validate_wiring.py::test_windows_use_nyc_calendar_day -v`
Expected: FAIL — cutoff computed from UTC "today" (Apr 2) excludes the fixture.

- [ ] **Step 3: Implement**

- `bills.py` `recent_bills`: replace `_dt.date.today()` with `today_nyc()` (import from `._validate`).
- `events.py` `upcoming_events`: replace both `_dt.date.today()` calls with a single `today = today_nyc()` and use it for both `today.isoformat()` and `(today + _dt.timedelta(days=days)).isoformat()`.
- `events.py` `get_bill_hearings`: replace `_dt.date.today().isoformat()` with `today_nyc().isoformat()`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS. Watch the pre-existing `@freeze_time("2024-04-01")` tests in `tests/test_tool_search_bills.py` and `tests/test_tool_events.py`: frozen midnight UTC is 20:00 Mar 31 in NYC, shifting "today" one day earlier. If any of those tests flips, change its decorator to `@freeze_time("2024-04-01 12:00:00")` (noon UTC = same NYC calendar day) — that preserves each test's original intent.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src tests
git add src/legistar_mcp/tools/ tests/
git commit -m "fix(tools): compute 'today' in America/New_York for recent/upcoming windows"
```

### Task 10: Guarded archive reads, guided not-found errors, bill-identifier consistency

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (`get_bill`), `src/legistar_mcp/tools/events.py` (`get_event`, `get_bill_hearings`), `src/legistar_mcp/tools/people.py` (`get_person`), `src/legistar_mcp/tools/relationships.py` (`co_sponsors`, `get_voting_record`, `vote_breakdown`), `src/legistar_mcp/tools/_validate.py` (add `resolve_bill_id`, `require_known_slug`, `load_archive_json`), `src/legistar_mcp/server.py` (`vote_breakdown` wrapper gains `file`)
- Test: `tests/test_validate_wiring.py`

**Interfaces:**
- Consumes: fixture slugs/files (`adrienne-e-adams`, `Int 0153-2022`).
- Produces (added to `tools/_validate.py`):
  - `resolve_bill_id(conn, file: str | None, id: int | None) -> int` — raises `ValueError` if neither given or the file is unknown.
  - `require_known_slug(conn, slug: str) -> None` — raises `ValueError` if the slug appears in none of `people`/`sponsors`/`votes`.
  - `load_archive_json(archive_root, rel_path: str) -> dict` — raises `ValueError` (not `FileNotFoundError`) with re-index guidance when the source file is missing/unreadable.
- Produces (tool contract): `get_bill`/`get_event`/`get_person` raise a guided `ValueError` for unknown identifiers instead of returning `None`; `vote_breakdown` accepts `file` OR `bill_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate_wiring.py`:

```python
def test_unknown_identifiers_raise_guided_errors(indexed_db, fixtures_root):
    from legistar_mcp.tools.bills import get_bill
    from legistar_mcp.tools.events import get_bill_hearings, get_event
    from legistar_mcp.tools.people import get_person
    from legistar_mcp.tools.relationships import co_sponsors, get_voting_record

    with pytest.raises(ValueError, match="search_bills"):
        get_bill(indexed_db, fixtures_root, file="Int 9999-2099")
    with pytest.raises(ValueError, match="search_events"):
        get_event(indexed_db, fixtures_root, id=999999999)
    with pytest.raises(ValueError, match="search_people"):
        get_person(indexed_db, fixtures_root, "nobody-here")
    with pytest.raises(ValueError, match="search_bills"):
        get_bill_hearings(indexed_db, file="Int 9999-2099")
    with pytest.raises(ValueError, match="search_people"):
        get_voting_record(indexed_db, slug="nobody-here")
    with pytest.raises(ValueError, match="search_people"):
        co_sponsors(indexed_db, slug="nobody-here")


def test_vote_breakdown_accepts_file(indexed_db):
    from legistar_mcp.tools.relationships import vote_breakdown
    by_file = vote_breakdown(indexed_db, file="Int 0153-2022")
    assert isinstance(by_file, list)
    with pytest.raises(ValueError, match="search_bills"):
        vote_breakdown(indexed_db, file="Int 9999-2099")


def test_missing_archive_file_is_guided_not_traceback(indexed_db, tmp_path, fixtures_root):
    from legistar_mcp.tools.bills import get_bill
    # Point the reader at a root where the indexed rel-path doesn't exist.
    with pytest.raises(ValueError, match="legistar-mcp index"):
        get_bill(indexed_db, tmp_path, file="Int 0153-2022")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate_wiring.py -v -k "unknown_identifiers or vote_breakdown_accepts or missing_archive"`
Expected: FAIL — `get_bill` returns `None`, `vote_breakdown` rejects `file` kwarg, missing archive file raises `FileNotFoundError`.

- [ ] **Step 3: Implement the helpers**

Append to `src/legistar_mcp/tools/_validate.py`:

```python
import json
from pathlib import Path


def resolve_bill_id(conn: Connection, file: str | None, id: int | None) -> int:
    """One identifier contract for every bill-taking tool: accept `file`
    (e.g. 'Int 0153-2022') or numeric `id`, and fail with next-step guidance
    instead of an ambiguous empty result."""
    if file is None and id is None:
        raise ValueError("Supply either `file` (e.g. 'Int 0153-2022') or numeric `id`.")
    if file is not None:
        row = conn.execute("SELECT id FROM bills WHERE file = ?", (file,)).fetchone()
        if row is None:
            raise ValueError(
                f"No bill with file {file!r}. Files look like 'Int 0153-2022' / "
                f"'Res 0021-2024'; find bills via search_bills."
            )
        return row["id"]
    return id


def require_known_slug(conn: Connection, slug: str) -> None:
    """Distinguish 'unknown person' from 'person with no rows'. Slugs can
    legitimately appear only in sponsors/votes (former members not in the
    people table), so check all three."""
    known = conn.execute(
        "SELECT 1 FROM people WHERE slug = ? "
        "UNION SELECT 1 FROM sponsors WHERE person_slug = ? "
        "UNION SELECT 1 FROM votes WHERE person_slug = ? LIMIT 1",
        (slug, slug, slug),
    ).fetchone()
    if known is None:
        raise ValueError(
            f"Unknown person slug {slug!r}. Find slugs via search_people(name=...)."
        )


def load_archive_json(archive_root: Path, rel_path: str) -> dict:
    """Archive reads for detail tools. The search tools already degrade
    gracefully when a source file vanished after indexing; detail tools were
    500'ing with a bare FileNotFoundError."""
    try:
        with open(Path(archive_root) / rel_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Indexed source file {rel_path!r} is missing or unreadable under "
            f"{archive_root} — the archive was likely moved or pruned after "
            f"indexing. Re-run `legistar-mcp index` to re-sync."
        ) from exc
```

(Move the `import json` / `from pathlib import Path` lines up into the module's import block — ruff will flag mid-file imports.)

- [ ] **Step 4: Wire the tools**

- `bills.py` `get_bill`: replace the body's lookup + open with:
  ```python
      bill_id = resolve_bill_id(conn, file, id)
      row = conn.execute("SELECT path FROM bills WHERE id = ?", (bill_id,)).fetchone()
      if not row:
          raise ValueError(f"No bill with id {bill_id}. Find bills via search_bills.")
      bill = load_archive_json(archive_root, row["path"])
      bill["LegistarURL"] = _legistar_url(bill.get("ID"))
      return bill
  ```
  Return annotation becomes `-> dict` (never `None` now).
- `events.py` `get_event`: unknown id → `raise ValueError(f"No event with id {id}. Find events via search_events or upcoming_events.")`; read via `load_archive_json`.
- `people.py` `get_person`: unknown slug → `raise ValueError(f"Unknown person slug {slug!r}. Find slugs via search_people(name=...).")`; read via `load_archive_json`.
- `events.py` `get_bill_hearings`: replace its file/id resolution block with `bill_id = resolve_bill_id(conn, file, id)` and delete the `if bill_id is None: return []` branch.
- `relationships.py`: `get_voting_record` and `co_sponsors` call `require_known_slug(conn, slug)` first. `vote_breakdown` signature becomes `def vote_breakdown(conn, bill_id: int | None = None, file: str | None = None, limit: int = 100)` with `bill_id = resolve_bill_id(conn, file, bill_id)` first.
- `server.py` `vote_breakdown` wrapper: signature `(bill_id: int | None = None, file: str | None = None, limit: int = 100)`, pass both through; description gains "Supply either numeric `bill_id` or bill `file` (e.g. 'Int 0153-2022')."
- Also update `server.py` `get_bill`/`get_event`/`get_person` return annotations from `dict | None` to `dict`.

- [ ] **Step 5: Run tests, fix collateral, commit**

Run: `uv run pytest -v`
Expected: the new tests PASS. `tests/test_tool_get_bill.py` / `test_tool_get_event.py` / `test_tool_people.py` may assert `is None` for unknown ids — update those assertions to `pytest.raises(ValueError)` (that contract change is this task's point).

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/ tests/
git commit -m "fix(tools): guided errors for unknown ids/slugs, guarded archive reads, vote_breakdown accepts file"
```

### Task 11: Case-insensitive filters + multi-word people search

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (`_bill_filters`, `recent_bills`), `src/legistar_mcp/tools/events.py` (`_event_filters`, `upcoming_events`), `src/legistar_mcp/tools/people.py` (`search_people`)
- Test: `tests/test_validate_wiring.py`

**Interfaces:**
- Produces: `status`/`type`/`committee` filters match case-insensitively; `search_people(name=...)` matches when every whitespace-separated token appears in `full_name`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate_wiring.py`:

```python
def test_filters_match_case_insensitively(indexed_db):
    exact = search_bills(indexed_db, status="Enacted", limit=10)
    lower = search_bills(indexed_db, status="enacted", limit=10)
    assert [r["file"] for r in lower] == [r["file"] for r in exact]
    assert lower, "fixture set contains an Enacted bill"


def test_search_people_matches_across_middle_initial(indexed_db):
    # full_name is 'Adrienne E. Adams' — a single-substring LIKE missed this.
    hits = search_people(indexed_db, name="Adrienne Adams")
    assert any(p["slug"] == "adrienne-e-adams" for p in hits)
```

(If no fixture bill is `Enacted`, check `python3 -c "import json;print(json.load(open('tests/fixtures/bills/int_0153_2022.json'))['StatusName'])"` and use that status value in both cases of the first test.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate_wiring.py -v -k "case_insensitively or middle_initial"`
Expected: both FAIL.

- [ ] **Step 3: Implement**

- `bills.py` `_bill_filters`: change `"bills.status_name = ?"` → `"bills.status_name = ? COLLATE NOCASE"`, `"bills.type_name = ?"` → `"bills.type_name = ? COLLATE NOCASE"`, `"bills.body_name = ?"` → `"bills.body_name = ? COLLATE NOCASE"`.
- `bills.py` `recent_bills`: same two changes for its inline `status_name` / `type_name` predicates.
- `events.py` `_event_filters` and `upcoming_events`: `"events.body_name = ?"` → `"events.body_name = ? COLLATE NOCASE"`.
- `people.py` `search_people`: replace the single-LIKE block with:
  ```python
      if name:
          for token in name.lower().split():
              where.append("LOWER(full_name) LIKE ?")
              params.append(f"%{token}%")
  ```

(21k-row full scans without the index are fine at this scale; do not add NOCASE indexes.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate_wiring.py tests/test_tool_people.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/tools/ tests/test_validate_wiring.py
git commit -m "fix(tools): case-insensitive status/type/committee filters; tokenized people search"
```

### Task 12: Snippet correctness (Unicode-safe matching) + mentions for plain queries

**Files:**
- Modify: `src/legistar_mcp/tools/_snippet.py` (`_build_snippet`), `src/legistar_mcp/tools/bills.py` (`search_bills` mentions gate), `src/legistar_mcp/tools/events.py` (`search_events` mentions gate)
- Test: `tests/test_validate_wiring.py`

**Interfaces:**
- Produces: `_build_snippet` matches case-insensitively via `re.search` on the original string (no `.lower()` offset drift); `search_bills`/`search_events` attach `mentions` whenever ANY text query ran (agency or free text), with the raw user query added to the phrase list.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate_wiring.py`:

```python
def test_snippet_offsets_survive_unicode_case_folding():
    from legistar_mcp.tools._snippet import _build_snippet
    # 'İ' (U+0130) lowercases to TWO chars — index math on text.lower()
    # previously misplaced the <mark>.
    text = "İİİİİ the police department shall report annually İİİİİ"
    snip = _build_snippet(text, ["police department"])
    assert snip is not None
    assert "<mark>police department</mark>" in snip


def test_plain_query_search_returns_mentions(indexed_db):
    rows = search_bills(indexed_db, query='"domestic violence"', limit=5)
    hit = next(r for r in rows if "0153-2022" in r["file"])
    assert hit["mentions"], "plain-text query should carry role-context snippets too"
    rows_bare = search_bills(indexed_db, query="domestic violence", limit=5)
    hit_bare = next(r for r in rows_bare if "0153-2022" in r["file"])
    assert hit_bare["mentions"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate_wiring.py -v -k "unicode_case or plain_query"`
Expected: the Unicode test FAILS (mark misaligned — assertion on exact `<mark>` content fails); the mentions test FAILS (`mentions` key absent for plain queries).

- [ ] **Step 3: Implement**

Replace `_build_snippet` in `src/legistar_mcp/tools/_snippet.py`:

```python
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
```

In `bills.py` `search_bills`, change the mentions gate from `if agency and rows:` to `if fts_query and rows:` and build the phrase list as:

```python
        phrases = _extract_phrases(fts_query)
        if query and query.strip():
            phrases.append(query.strip())
```

Mirror in `events.py` `search_events` (same gate change, same phrase append; the per-event dedupe/cap logic stays untouched).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate_wiring.py tests/test_tool_search_bills.py tests/test_tool_events.py -v`
Expected: all PASS — including the pre-existing agency-snippet test (agency phrases still lead the list).

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/tools/ tests/test_validate_wiring.py
git commit -m "fix(snippets): unicode-safe match offsets; mentions for plain-text queries"
```

**Milestone 2 checkpoint:** push `fix/query-validation`, open PR titled `fix: query-layer validation — FTS hygiene, input validation, guided errors`.

---

## Milestone 3 — Indexer integrity

**Branch:** `fix/indexer-integrity`.

Review findings addressed: #13 (LastModified None==None skip), #14 (no purge of removed files), #15 (StaleIndexError misfires), plus the below-cap `AgendaSequence=0` falsy-zero and the missing `last_indexed` timestamp.

### Task 13: Incremental skip must not treat `None == None` as "unchanged"

**Files:**
- Modify: `src/legistar_mcp/index/bulk.py:96-112` (both skip conditions)
- Test: `tests/test_index_bulk.py`

**Interfaces:**
- Produces: files whose JSON lacks `LastModified` are always (re)indexed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_bulk.py`:

```python
def test_incremental_indexes_files_without_lastmodified(tmp_path, fixtures_root):
    """A record with no LastModified previously compared None == None against
    the 'seen' map and was skipped forever — never indexed at all."""
    import json
    import shutil
    from legistar_mcp.db import init_db

    archive = tmp_path / "archive"
    shutil.copytree(fixtures_root, archive)
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=archive, incremental=False)

    src = json.loads((archive / "bills" / "int_0001_2024.json").read_text())
    src["ID"] = 999001
    src["File"] = "Int 9990-2024"
    src.pop("LastModified", None)
    (archive / "bills" / "no_lastmod.json").write_text(json.dumps(src))

    build_all(conn, archive_root=archive, incremental=True)
    row = conn.execute("SELECT file FROM bills WHERE id = 999001").fetchone()
    assert row is not None and row["file"] == "Int 9990-2024"
```

Note: the fixtures directory nests bills under `fixtures/bills/` and the walker's fallback `(root / "bills").glob("*.json")` handles that layout (`_bill_paths`), so the copied tree indexes the same way — verify with the assertion, not by assumption.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index_bulk.py::test_incremental_indexes_files_without_lastmodified -v`
Expected: FAIL — row is None (file skipped via `None == None`).

- [ ] **Step 3: Implement**

In `build_all`, replace both skip conditions. Bills:

```python
            if incremental:
                rel = p.resolve().relative_to(archive_resolved).as_posix()
                lm = _last_modified_of(p)
                # `lm is None` must never match: a file with no LastModified
                # would compare None == None against a never-seen path and be
                # skipped forever. Unknown paths and None stamps always index.
                if rel in seen_bills and lm is not None and seen_bills[rel] == lm:
                    continue
```

Events: identical shape with `seen_events`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_bulk.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/index/bulk.py tests/test_index_bulk.py
git commit -m "fix(index): never skip files lacking LastModified in incremental mode"
```

### Task 14: Purge rows for files removed from the archive

**Files:**
- Modify: `src/legistar_mcp/index/bulk.py` (`build_all` — purge pass after the walks)
- Test: `tests/test_index_bulk.py`

**Interfaces:**
- Produces: after every `build_all` run (incremental or full), rows whose `path` no longer exists in the walk are deleted, cascading: bills → sponsors, votes, bills_fts (via map), bills_fts_map; events → event_items, events_fts (via map), events_fts_map; people. `stats` gains `"removed"` (total rows purged across the three tables).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_bulk.py`:

```python
def test_removed_archive_files_are_purged_on_reindex(tmp_path, fixtures_root):
    """Upstream deletes/renames a JSON → the row previously survived every
    reindex (even --full), leaving phantom bills searchable forever."""
    import shutil
    from legistar_mcp.db import init_db

    archive = tmp_path / "archive"
    shutil.copytree(fixtures_root, archive)
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=archive, incremental=False)
    assert conn.execute(
        "SELECT 1 FROM bills WHERE file = 'Int 0153-2022'"
    ).fetchone() is not None

    (archive / "bills" / "int_0153_2022.json").unlink()
    stats = build_all(conn, archive_root=archive, incremental=False)

    assert stats["removed"] >= 1
    assert conn.execute(
        "SELECT 1 FROM bills WHERE file = 'Int 0153-2022'"
    ).fetchone() is None
    # Cascade: no orphaned sponsors/votes/FTS-map rows may remain.
    assert conn.execute(
        "SELECT COUNT(*) FROM sponsors s LEFT JOIN bills b ON s.bill_id = b.id "
        "WHERE b.id IS NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM bills_fts_map m LEFT JOIN bills b ON m.bill_id = b.id "
        "WHERE b.id IS NULL"
    ).fetchone()[0] == 0
    # FTS content is gone too: an FTS-backed search must no longer surface the
    # purged bill (behavioral check — robust even if other fixtures also match
    # the phrase).
    from legistar_mcp.tools.bills import search_bills
    remaining = search_bills(conn, query='"domestic violence"', limit=10)
    assert not any("0153-2022" in r["file"] for r in remaining)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index_bulk.py::test_removed_archive_files_are_purged_on_reindex -v`
Expected: FAIL — KeyError `'removed'` or the phantom row still present.

- [ ] **Step 3: Implement the purge pass**

In `build_all`, after the three walk loops and before the `user_version` bump, add:

```python
    # Purge rows whose source file vanished from the archive (upstream delete
    # or rename). Without this, phantom records survive every reindex — even
    # --full only ever INSERT-OR-REPLACEs what the walk finds.
    stats["removed"] = _purge_missing(
        conn, archive_resolved, bills=bills, events=events, people=people
    )
```

Add the helper above `build_all`:

```python
def _purge_missing(
    conn: Connection,
    archive_resolved: Path,
    *,
    bills: list[Path],
    events: list[Path],
    people: list[Path],
) -> int:
    """Delete rows (and their dependents) whose `path` was not walked this run.
    Runs on every build; on an unchanged archive the NOT-IN sets are empty and
    this is a cheap no-op."""

    def rels(paths: list[Path]) -> set[str]:
        return {p.resolve().relative_to(archive_resolved).as_posix() for p in paths}

    removed = 0
    with conn:
        walked = rels(bills)
        gone = [
            r["id"]
            for r in conn.execute("SELECT id, path FROM bills")
            if r["path"] not in walked
        ]
        for bid in gone:
            fts = conn.execute(
                "SELECT fts_rowid FROM bills_fts_map WHERE bill_id = ?", (bid,)
            ).fetchone()
            if fts:
                conn.execute("DELETE FROM bills_fts WHERE rowid = ?", (fts["fts_rowid"],))
            conn.execute("DELETE FROM bills_fts_map WHERE bill_id = ?", (bid,))
            conn.execute("DELETE FROM sponsors WHERE bill_id = ?", (bid,))
            conn.execute("DELETE FROM votes WHERE bill_id = ?", (bid,))
            conn.execute("DELETE FROM bills WHERE id = ?", (bid,))
        removed += len(gone)

        walked = rels(events)
        gone = [
            r["id"]
            for r in conn.execute("SELECT id, path FROM events")
            if r["path"] not in walked
        ]
        for eid in gone:
            for m in conn.execute(
                "SELECT fts_rowid FROM events_fts_map WHERE event_id = ?", (eid,)
            ).fetchall():
                conn.execute("DELETE FROM events_fts WHERE rowid = ?", (m["fts_rowid"],))
            conn.execute("DELETE FROM events_fts_map WHERE event_id = ?", (eid,))
            conn.execute("DELETE FROM event_items WHERE event_id = ?", (eid,))
            conn.execute("DELETE FROM events WHERE id = ?", (eid,))
        removed += len(gone)

        walked = rels(people)
        gone = [
            r["slug"]
            for r in conn.execute("SELECT slug, path FROM people")
            if r["path"] not in walked
        ]
        for slug in gone:
            conn.execute("DELETE FROM people WHERE slug = ?", (slug,))
        removed += len(gone)
    return removed
```

Also update `cli.py`'s summary line to include it:

```python
    click.echo(
        f"Indexed: bills={stats['bills']} events={stats['events']} "
        f"people={stats['people']} removed={stats['removed']}"
    )
```

(README shows the old `Indexed:` line in two places — update both examples to include `removed=0`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_bulk.py tests/test_index_bills.py tests/test_index_events.py -v`
Expected: all PASS. `tests/test_server.py` or others may assert on the `Indexed:` line format — run the full suite and fix any string assertions.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/index/bulk.py src/legistar_mcp/cli.py tests/ README.md
git commit -m "fix(index): purge rows for files removed upstream, with full FTS/dependent cascade"
```

### Task 15: `AgendaSequence=0` falsy-zero, per-feature stale gate, SCHEMA_VERSION 5

**Files:**
- Modify: `src/legistar_mcp/index/build.py:122` (sequence coalescing), `src/legistar_mcp/_db_utils.py` (`_check_table_populated` gains `min_version`), `src/legistar_mcp/db.py` (SCHEMA_VERSION → 5 + history note), `src/legistar_mcp/tools/events.py` + `src/legistar_mcp/tools/relationships.py` (pass `min_version`)
- Test: `tests/test_index_events.py`, `tests/test_db_utils.py`

**Interfaces:**
- Produces: `_check_table_populated(conn, table, related_table, min_version)` — raises only when `user_version < min_version` (the version that introduced that table): `event_items` → 2, `votes` → 3.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_events.py`:

```python
def test_agenda_sequence_zero_is_preserved(tmp_path, fixtures_root):
    """`AgendaSequence or MinutesSequence or 0` discarded a legitimate 0 and
    fell through to MinutesSequence."""
    import json
    import shutil
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    archive = tmp_path / "archive"
    shutil.copytree(fixtures_root, archive)
    event_file = next((archive / "events").rglob("*.json"))
    e = json.loads(event_file.read_text())
    items = e.get("Items") or []
    assert items, "fixture event must have at least one item"
    items[0]["AgendaSequence"] = 0
    items[0]["MinutesSequence"] = 7  # the wrong fallback the bug would take
    event_file.write_text(json.dumps(e))

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=archive, incremental=False)
    seq = conn.execute(
        "SELECT item_sequence FROM event_items WHERE event_id = ? ORDER BY item_id LIMIT 1",
        (e["ID"],),
    ).fetchone()
    if seq is not None:  # item only mirrors when it has a MatterID
        assert seq["item_sequence"] == 0
    fts_seq = conn.execute(
        "SELECT item_sequence FROM events_fts_map WHERE event_id = ? ORDER BY fts_rowid LIMIT 1",
        (e["ID"],),
    ).fetchone()
    assert fts_seq["item_sequence"] == 0
```

Append to `tests/test_db_utils.py`:

```python
def test_stale_gate_is_per_feature_not_global(tmp_path, fixtures_root):
    """A DB fully indexed at v3 (votes + event_items populated) must NOT raise
    StaleIndexError for those tables when the code's global version moves to
    5 for unrelated reasons."""
    from legistar_mcp._db_utils import _check_table_populated
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all

    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    conn.execute("PRAGMA user_version = 3")  # pretend last full index was v3
    conn.commit()
    # votes was introduced at v3; a v3 DB is complete for it — must not raise.
    _check_table_populated(conn, "votes", "bills", min_version=3)
    _check_table_populated(conn, "event_items", "events", min_version=2)
```

Check `tests/test_db_utils.py`'s existing tests first — they call `_check_table_populated` with the old 3-arg signature and will need `min_version=` added with the values used at each call site.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_events.py::test_agenda_sequence_zero_is_preserved tests/test_db_utils.py -v`
Expected: sequence test FAILS (`item_sequence == 7`); gate test FAILS (`TypeError` on `min_version` or `StaleIndexError` raised).

- [ ] **Step 3: Implement**

`src/legistar_mcp/index/build.py:122` — replace:

```python
            seq = item.get("AgendaSequence")
            if seq is None:
                seq = item.get("MinutesSequence")
            if seq is None:
                seq = 0
```

`src/legistar_mcp/_db_utils.py` — signature `def _check_table_populated(conn, table, related_table, min_version):`; replace the version comparison with:

```python
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version >= min_version:
        return  # the release that introduced `table` has been fully indexed
```

and update the docstring + error text to say "…is at version {current_version}, `{table}` requires a full index from version {min_version}+".

Call sites: `events.py` `get_bill_hearings` / `get_event_bills` → `_check_table_populated(conn, "event_items", "events", min_version=2)`; `relationships.py` `get_voting_record` / `vote_breakdown` → `_check_table_populated(conn, "votes", "bills", min_version=3)`.

`src/legistar_mcp/db.py` — `SCHEMA_VERSION = 5` and append to the version-history comment:

```python
#   5 — event_items.item_sequence falsy-zero fix + stale-row purge +
#       index_state.last_indexed. --full backfills corrected sequences and
#       clears any phantom rows from pre-purge releases.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src tests
git add src/legistar_mcp/ tests/
git commit -m "fix(index): preserve AgendaSequence=0; per-feature stale gate; SCHEMA_VERSION 5"
```

### Task 16: Record `last_indexed` in index_state

**Files:**
- Modify: `src/legistar_mcp/index/bulk.py` (`build_all`)
- Test: `tests/test_index_bulk.py`

**Interfaces:**
- Produces: after every successful `build_all`, `index_state` has key `last_indexed` = UTC ISO timestamp. Milestone 4's `data_status` reads it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_bulk.py`:

```python
def test_build_all_records_last_indexed(tmp_path, fixtures_root):
    import datetime
    from legistar_mcp.db import init_db
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root, incremental=False)
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'last_indexed'"
    ).fetchone()
    assert row is not None
    stamp = datetime.datetime.fromisoformat(row["value"])
    assert stamp.tzinfo is not None  # stored as aware UTC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index_bulk.py::test_build_all_records_last_indexed -v`
Expected: FAIL — row is None.

- [ ] **Step 3: Implement**

In `build_all`, immediately before the final `conn.commit()`:

```python
    conn.execute(
        "INSERT OR REPLACE INTO index_state (key, value) VALUES ('last_indexed', ?)",
        (_dt.datetime.now(_dt.timezone.utc).isoformat(),),
    )
```

Add `import datetime as _dt` to the module imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_bulk.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/index/bulk.py tests/test_index_bulk.py
git commit -m "feat(index): record last_indexed timestamp in index_state"
```

**Milestone 3 checkpoint:** push `fix/indexer-integrity`, open PR titled `fix: indexer integrity — purge removed files, LastModified edge, schema v5`.

---

## Milestone 4 — Agent ergonomics & new tools

**Branch:** `feat/agent-ergonomics`.

Delivers: envelope outputs + offset paging (user-approved shape `{"results", "total", "offset", "truncated"}`), parameter descriptions, server instructions, read-only tool annotations, `list_agencies`, `data_status`, `get_bill_text`, staleness warnings, README/version/CI.

### Task 17: Envelope + offset on every list tool

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py`, `src/legistar_mcp/tools/events.py`, `src/legistar_mcp/tools/people.py`, `src/legistar_mcp/tools/relationships.py`, `src/legistar_mcp/tools/committees.py`, `src/legistar_mcp/tools/vocab.py` (no change — stays `list[str]`), `src/legistar_mcp/tools/_aggregate.py` (`run_aggregate` returns envelope; `validate_group_by` allows `[]`), `src/legistar_mcp/server.py` (signatures/annotations/offsets)
- Test: every `tests/test_tool_*.py`, `tests/test_validate_wiring.py`, `tests/test_integration_moo.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `envelope`, `clamp_offset` from Task 5.
- Produces:
  - Envelope + `offset: int = 0` param: `search_bills`, `search_events`, `search_people`, `recent_bills`, `upcoming_events`, `get_bill_hearings`, `get_voting_record`, `vote_breakdown`, `co_sponsors`, `aggregate_bills`, `aggregate_events`.
  - Envelope, no offset param: `list_committees`, `get_event_bills`.
  - Unchanged shapes: `get_bill`, `get_event`, `get_person` (dict), `list_vocabulary` (list[str] — always complete).
  - `aggregate_*` accept `group_by=[]` → single grand-total row `[{"count": N}]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate_wiring.py`:

```python
def test_search_bills_envelope_and_offset(indexed_db):
    page1 = search_bills(indexed_db, limit=2)
    assert set(page1) == {"results", "total", "offset", "truncated"}
    assert page1["total"] == 3 and len(page1["results"]) == 2 and page1["truncated"]
    page2 = search_bills(indexed_db, limit=2, offset=2)
    assert len(page2["results"]) == 1 and page2["offset"] == 2
    assert not page2["truncated"]
    ids = {r["id"] for r in page1["results"]} | {r["id"] for r in page2["results"]}
    assert len(ids) == 3  # pages don't overlap


def test_aggregate_empty_group_by_is_grand_total(indexed_db):
    from legistar_mcp.tools.bills import aggregate_bills
    out = aggregate_bills(indexed_db, group_by=[])
    assert out["results"] == [{"count": 3}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate_wiring.py -v -k "envelope or grand_total"`
Expected: FAIL — tools still return bare lists; empty group_by raises.

- [ ] **Step 3: Implement the pattern (complete example: `search_bills`)**

The per-tool recipe: (1) add `offset: int = 0`, clamp it; (2) run a COUNT over the same FROM/JOIN/WHERE; (3) add `OFFSET ?` after `LIMIT ?`; (4) return `envelope(rows, total, offset)`. Complete diff for `search_bills` — after the existing WHERE assembly and before the ORDER BY:

```python
    offset = clamp_offset(offset)
    count_sql = "SELECT COUNT(DISTINCT bills.id) FROM bills"
    if joins:
        count_sql += " " + " ".join(joins)
    if where:
        count_sql += " WHERE " + " AND ".join(where)
    total = conn.execute(count_sql, params).fetchone()[0]

    sql += " ORDER BY bills.intro_date DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
```

…and the final line becomes `return envelope(rows, total, offset)` (the mentions block operates on `rows` before the return, unchanged).

Apply the same recipe to each tool, with these exact COUNT expressions:

| Tool | COUNT query | offset param |
|---|---|---|
| `search_bills` | `SELECT COUNT(DISTINCT bills.id) FROM bills {joins} {where}` | yes |
| `search_events` | `SELECT COUNT(DISTINCT events.id) FROM events {joins} {where}` | yes |
| `search_people` | `SELECT COUNT(*) FROM people {where}` | yes |
| `recent_bills` | `SELECT COUNT(*) FROM bills WHERE …` (same predicates) | yes |
| `upcoming_events` | `SELECT COUNT(*) FROM events WHERE …` (same predicates) | yes |
| `get_bill_hearings` | `SELECT COUNT(*) FROM event_items ei JOIN events ON ei.event_id = events.id WHERE …` | yes |
| `get_voting_record` | `SELECT COUNT(*) FROM votes v WHERE …` (drop the bills join — it's a LEFT JOIN on a 1:1 key, count is join-free) | yes |
| `vote_breakdown` | `SELECT COUNT(*) FROM votes WHERE bill_id = ?` | yes |
| `co_sponsors` | `SELECT COUNT(*) FROM (…existing query without ORDER BY/LIMIT…)` (HAVING requires the subquery form) | yes |
| `list_committees` | `total = len(rows)` after fetch (result is small and unbounded by design) | no |
| `get_event_bills` | `total = len(rows)` after fetch | no |
| `aggregate_bills` / `aggregate_events` | in `run_aggregate`: `SELECT COUNT(*) FROM (SELECT 1 FROM {table} {joins} {where} GROUP BY {group_by})`; for `group_by=[]` total is `1` | yes |

In `_aggregate.py`:
- `validate_group_by`: delete the empty-list rejection; keep the allow-list loop. Update its docstring: empty means grand total.
- `run_aggregate`: accept `offset: int = 0`; when `group_by` is empty, `select_cols = []` and the SQL becomes `SELECT {count_expr} AS count FROM {table} {joins} {where}` with no GROUP BY/ORDER BY-by-dims (order by nothing; single row); otherwise unchanged plus `OFFSET ?`. Return `envelope(rows, total, offset)`.

In `server.py`: add `offset: int = 0` to the wrappers of every "yes" row above, pass it through, and change all their return annotations from `list[dict]` to `dict`. `list_committees` / `get_event_bills` return `dict` too.

- [ ] **Step 4: Update existing tests mechanically**

Find every direct indexing of tool results:

```bash
grep -rn "search_bills(\|search_events(\|search_people(\|recent_bills(\|upcoming_events(\|get_bill_hearings(\|get_voting_record(\|vote_breakdown(\|co_sponsors(\|aggregate_bills(\|aggregate_events(\|list_committees(\|get_event_bills(" tests/
```

For each call whose result is used as a list, append `["results"]` to the call expression (e.g. `results = search_bills(...)["results"]`). Files expected to need edits: `test_tool_search_bills.py`, `test_tool_events.py`, `test_tool_people.py`, `test_tool_committees.py`, `test_tool_aggregate_bills.py`, `test_tool_aggregate_events.py`, `test_tool_relationships.py`, `test_tool_voting.py`, `test_integration_moo.py`, `test_server.py`, and the earlier additions in `test_validate_wiring.py` (Tasks 6–12 — update those too, e.g. `len(search_bills(indexed_db, limit=-1)["results"]) == 1`).

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -v`
Expected: all PASS.

```bash
uv run ruff check src tests
git add src/legistar_mcp/ tests/
git commit -m "feat(tools): result envelopes with totals + offset paging on all list tools"
```

### Task 18: Parameter descriptions, server instructions, read-only annotations

**Files:**
- Modify: `src/legistar_mcp/server.py` (every tool signature + FastMCP construction), `pyproject.toml` (`mcp>=1.8`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: FastMCP `instructions=`, `mcp.types.ToolAnnotations` (verified importable in this venv).
- Produces: every published parameter carries a `description` in the JSON schema; all tools declare `readOnlyHint=True, openWorldHint=False`; the server publishes workflow instructions.

- [ ] **Step 1: Confirm the installed FastMCP supports both features**

Run: `uv run python -c "import inspect; from mcp.server.fastmcp import FastMCP; from mcp.types import ToolAnnotations; s=inspect.signature(FastMCP.tool); print('annotations' in s.parameters, 'instructions' in inspect.signature(FastMCP.__init__).parameters)"`
Expected: `True True`. If either is False: bump `mcp>=1.8` in `pyproject.toml`, `uv sync`, re-check.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_server.py` (mirror its existing env/monkeypatch setup for `LEGISTAR_DB_PATH` — read the file first; it already builds a server against a fixtures DB):

```python
import anyio


def test_tools_publish_param_descriptions_and_readonly_annotations(tmp_path, fixtures_root, monkeypatch):
    from legistar_mcp.db import init_db
    from legistar_mcp.index.bulk import build_all
    from legistar_mcp.server import make_server

    db = tmp_path / "t.db"
    conn = init_db(db)
    build_all(conn, archive_root=fixtures_root)
    conn.close()
    monkeypatch.setenv("LEGISTAR_DB_PATH", str(db))
    server = make_server()
    tools = anyio.run(server.list_tools)
    assert tools
    for t in tools:
        assert t.annotations and t.annotations.readOnlyHint is True, t.name
        for pname, pschema in t.inputSchema.get("properties", {}).items():
            assert pschema.get("description"), f"{t.name}.{pname} lacks a description"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL on the first tool — no annotations, no descriptions.

- [ ] **Step 4: Implement**

In `server.py`:

1. Imports:
   ```python
   from typing import Annotated, Literal

   from pydantic import Field

   from mcp.server.fastmcp import FastMCP
   from mcp.types import ToolAnnotations

   _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
   ```
2. Construction:
   ```python
       server = FastMCP(
           "legistar-mcp",
           instructions=(
               "NYC City Council legislation: bills, hearings/events, council "
               "members, and roll-call votes, indexed locally from the "
               "jehiah/nyc_legislation archive (1998-present). Read-only.\n"
               "- Call data_status first when freshness matters: the index is "
               "only as new as the user's last `legistar-mcp index` run.\n"
               "- Discover exact filter spellings with list_vocabulary "
               "(statuses, types, committees) and list_agencies (95 NYC "
               "agencies with aliases); string filters match case-insensitively.\n"
               "- For agency questions prefer search_bills/search_events with "
               "agency=… — it expands aliases and returns role-context "
               "`mentions` snippets. Combine with query=… to narrow.\n"
               "- List tools return {results, total, offset, truncated}; page "
               "with offset. Every result row carries legistar_url — cite it.\n"
               "- Empty `mentions` on an FTS hit means stemmed (non-literal) "
               "match, not a false positive; use get_bill_text for the passage."
           ),
       )
   ```
3. Every `@server.tool()` becomes `@server.tool(annotations=_RO)`.
4. Every parameter gains an `Annotated[…, Field(description="…")]`. Complete replacement signatures (types unchanged from Task 17's state; descriptions verbatim):

```python
    def search_bills(
        query: Annotated[str | None, Field(description="Free-text search over bill name/title/summary/full text. Plain words or quoted phrases; FTS5 operators (OR, NEAR) allowed. Combines (AND) with agency.")] = None,
        agency: Annotated[str | None, Field(description="NYC agency name, acronym, or alias (e.g. 'NYPD', 'Department of Consumer Affairs'). Resolved against list_agencies; adds role-context `mentions` snippets to each hit.")] = None,
        year_from: Annotated[int | None, Field(description="Earliest intro year, inclusive. 4-digit, e.g. 2022.")] = None,
        year_to: Annotated[int | None, Field(description="Latest intro year, inclusive. 4-digit, e.g. 2024.")] = None,
        status: Annotated[str | None, Field(description="Exact status name, case-insensitive (e.g. 'Enacted'). Discover values via list_vocabulary('status_name').")] = None,
        type: Annotated[str | None, Field(description="Exact bill type, case-insensitive (e.g. 'Introduction', 'Resolution'). Discover via list_vocabulary('type_name').")] = None,
        committee: Annotated[str | None, Field(description="Exact committee (body) name, case-insensitive. Discover via list_vocabulary('body_name').")] = None,
        sponsor_slug: Annotated[str | None, Field(description="Council-member slug (e.g. 'adrienne-e-adams'). Find via search_people.")] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging; use with `total` from a prior call.")] = 0,
    ) -> dict:
        """Search NYC Council bills. Returns {results, total, offset, truncated}; each result has file, title, summary, status, type, committee, intro_date, legistar_url, and — when agency/query is set — `mentions` snippets quoting the matching statutory text."""
```

```python
    def get_bill(
        file: Annotated[str | None, Field(description="Bill file number, e.g. 'Int 0153-2022' or 'Res 0021-2024'.")] = None,
        id: Annotated[int | None, Field(description="Numeric Legistar matter ID (the `id` field from search_bills results).")] = None,
    ) -> dict:
        """Fetch one bill's full source record (sponsors, history, attachments, votes, full text). Responses can be large — for just the statutory text around a phrase, prefer get_bill_text. Supply `file` or `id`; unknown identifiers raise an error naming the fix."""
```

```python
    def search_people(
        name: Annotated[str | None, Field(description="Name words, any order; all must appear (e.g. 'Adrienne Adams' matches 'Adrienne E. Adams').")] = None,
        active_only: Annotated[bool, Field(description="True = only currently serving members.")] = False,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Search council members. Returns {results, total, offset, truncated}; each result has slug, full_name, is_active, start/end dates. Slugs feed get_person, search_bills(sponsor_slug), get_voting_record, co_sponsors."""
```

```python
    def get_person(
        slug: Annotated[str, Field(description="Member slug from search_people, e.g. 'adrienne-e-adams'.")],
    ) -> dict:
        """Fetch a council member's full profile plus sponsored-bill counts grouped by bill status (under `_stats`)."""
```

```python
    def search_events(
        query: Annotated[str | None, Field(description="Free-text search over agenda item titles and agenda/minutes notes. Combines (AND) with agency.")] = None,
        agency: Annotated[str | None, Field(description="NYC agency name or alias; adds `mentions` snippets from matching agenda items.")] = None,
        date_from: Annotated[str | None, Field(description="Earliest event date, ISO: YYYY, YYYY-MM, or YYYY-MM-DD.")] = None,
        date_to: Annotated[str | None, Field(description="Latest event date, inclusive of the whole named period. ISO: YYYY, YYYY-MM, or YYYY-MM-DD.")] = None,
        committee: Annotated[str | None, Field(description="Exact committee (body) name, case-insensitive. Discover via list_vocabulary('event_committee').")] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Search committee hearings and Council meetings. Returns {results, total, offset, truncated}; each result has id, body_name, date, location, legistar_url, and `mentions` when agency/query is set."""
```

```python
    def get_event(
        id: Annotated[int, Field(description="Numeric event ID from search_events/upcoming_events results.")],
    ) -> dict:
        """Fetch one event's full source record: agenda items, minutes notes, per-item actions and votes. Large for full Council meetings."""
```

```python
    def list_committees(
        year_from: Annotated[int | None, Field(description="Restrict counts to bills introduced / events held from this year, inclusive.")] = None,
        year_to: Annotated[int | None, Field(description="Restrict counts through this year, inclusive.")] = None,
    ) -> dict:
        """All committees with bill/event counts and first-seen dates (earliest activity in the archive — a lower bound, not an establishment date). Returns {results, total, offset, truncated} sorted by activity."""
```

```python
    def aggregate_bills(
        group_by: Annotated[list[GroupByDim], Field(description="Dimensions to group by, e.g. ['intro_year'] or ['status_name','intro_year']. Empty list = one grand-total row.")],
        query: Annotated[str | None, Field(description="Free-text filter, same semantics as search_bills.query.")] = None,
        year_from: Annotated[int | None, Field(description="Earliest intro year, inclusive (4-digit).")] = None,
        year_to: Annotated[int | None, Field(description="Latest intro year, inclusive (4-digit).")] = None,
        status: Annotated[str | None, Field(description="Exact status name, case-insensitive; see list_vocabulary('status_name').")] = None,
        type: Annotated[str | None, Field(description="Exact bill type, case-insensitive; see list_vocabulary('type_name').")] = None,
        committee: Annotated[str | None, Field(description="Exact committee name, case-insensitive; see list_vocabulary('body_name').")] = None,
        sponsor_slug: Annotated[str | None, Field(description="Council-member slug; find via search_people.")] = None,
        agency: Annotated[str | None, Field(description="NYC agency name or alias (FTS join — slower on broad windows).")] = None,
        limit: Annotated[int, Field(description="Max groups returned, clamped to 1-1000.")] = 100,
        offset: Annotated[int, Field(description="Groups to skip for paging.")] = 0,
    ) -> dict:
        """Group bills by one or more dimensions (status_name, type_name, body_name, sponsor_slug, intro_year) and return per-group counts, largest first. Filters: query, agency, year_from/year_to, status, type, committee, sponsor_slug."""
```

```python
    def aggregate_events(
        group_by: Annotated[list[EventGroupByDim], Field(description="Dimensions to group by: body_name, event_year, event_month (YYYY-MM). Empty list = one grand-total row.")],
        query: Annotated[str | None, Field(description="Free-text filter, same semantics as search_events.query.")] = None,
        date_from: Annotated[str | None, Field(description="Earliest event date, ISO: YYYY, YYYY-MM, or YYYY-MM-DD.")] = None,
        date_to: Annotated[str | None, Field(description="Latest event date, inclusive of the whole named period.")] = None,
        committee: Annotated[str | None, Field(description="Exact committee name, case-insensitive.")] = None,
        agency: Annotated[str | None, Field(description="NYC agency name or alias (FTS join — slower on broad windows).")] = None,
        limit: Annotated[int, Field(description="Max groups returned, clamped to 1-1000.")] = 100,
        offset: Annotated[int, Field(description="Groups to skip for paging.")] = 0,
    ) -> dict:
        """Group events by one or more dimensions (body_name, event_year, event_month) and return per-group counts, largest first. Filters: query, agency, date_from/date_to, committee."""
```

```python
    def list_vocabulary(
        field: Annotated[VocabField, Field(description="Which column's distinct values to list: status_name / type_name / body_name (bills) or event_committee (events).")],
    ) -> list[str]:
        """Every distinct value for a filter column — use before filtering by status/type/committee to get exact spellings. Complete list, no paging. For agencies use list_agencies instead."""
```

```python
    def recent_bills(
        days: Annotated[int, Field(description="Window size in days back from today (NYC time). Positive integer.")] = 7,
        status: Annotated[str | None, Field(description="Exact status name, case-insensitive.")] = None,
        type: Annotated[str | None, Field(description="Exact bill type, case-insensitive.")] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Bills introduced in the last `days` days, newest first. Includes a `warning` field when the local index looks stale. For agency-scoped searches use search_bills(agency=…)."""
```

```python
    def upcoming_events(
        days: Annotated[int, Field(description="Window size in days ahead from today (NYC time). Positive integer.")] = 14,
        committee: Annotated[str | None, Field(description="Exact committee (body) name, case-insensitive.")] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Events scheduled in the next `days` days, soonest first. Includes a `warning` field when the local index looks stale — a stale index can miss newly scheduled hearings."""
```

```python
    def co_sponsors(
        slug: Annotated[str, Field(description="Member slug from search_people.")],
        min_overlap: Annotated[int, Field(description="Only return members who co-sponsored at least this many bills together.")] = 5,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Council members who most often co-sponsor bills with the given member, sorted by shared-bill count."""
```

```python
    def get_bill_hearings(
        file: Annotated[str | None, Field(description="Bill file number, e.g. 'Int 0153-2022'.")] = None,
        id: Annotated[int | None, Field(description="Numeric bill ID.")] = None,
        only_upcoming: Annotated[bool, Field(description="True = only future events, soonest first; False = full history, newest first.")] = False,
        limit: Annotated[int, Field(description="Max results, clamped to 1-200.")] = 20,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Events where the given bill was on the agenda, with per-item action names. Supply `file` or `id`."""
```

```python
    def get_event_bills(
        event_id: Annotated[int, Field(description="Numeric event ID from search_events/upcoming_events.")],
    ) -> dict:
        """Bills on a specific event's agenda in agenda order, each with item title, sequence, action, and legistar_url."""
```

```python
    def get_voting_record(
        slug: Annotated[str, Field(description="Member slug from search_people.")],
        year_from: Annotated[int | None, Field(description="Earliest vote year, inclusive (4-digit).")] = None,
        year_to: Annotated[int | None, Field(description="Latest vote year, inclusive (4-digit).")] = None,
        vote_value: Annotated[str | None, Field(description="Filter to one outcome: 'Affirmative', 'Negative', 'Absent', 'Abstain', 'Excused', …")] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-1000.")] = 100,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Every vote the member cast, newest first, with the bill's file/title/status and the action voted on."""
```

```python
    def vote_breakdown(
        bill_id: Annotated[int | None, Field(description="Numeric bill ID.")] = None,
        file: Annotated[str | None, Field(description="Bill file number, e.g. 'Int 0153-2022'.")] = None,
        limit: Annotated[int, Field(description="Max results, clamped to 1-1000. Raise for omnibus bills.")] = 100,
        offset: Annotated[int, Field(description="Rows to skip for paging.")] = 0,
    ) -> dict:
        """Every council member's vote on one bill across all its roll calls, newest action first. Supply `bill_id` or `file`."""
```

(Keep each wrapper's body — the `_db_locked` call-through — unchanged apart from new parameters added in earlier tasks.)

- [ ] **Step 5: Run tests, commit**

Run: `uv run pytest tests/test_server.py -v && uv run pytest`
Expected: all PASS.

```bash
uv run ruff check src tests
git add src/legistar_mcp/server.py pyproject.toml uv.lock tests/test_server.py
git commit -m "feat(server): param descriptions, server instructions, read-only tool annotations"
```

### Task 19: `list_agencies` tool

**Files:**
- Modify: `src/legistar_mcp/tools/vocab.py` (add `list_agencies`), `src/legistar_mcp/server.py` (register)
- Test: `tests/test_tool_vocab.py`

**Interfaces:**
- Produces: `list_agencies(query: str | None = None) -> dict` in `vocab.py` — takes no `conn` (reads the packaged YAML, no DB needed); returns the standard envelope.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tool_vocab.py`:

```python
def test_list_agencies_returns_catalog_and_filters():
    from legistar_mcp.tools.vocab import list_agencies
    out = list_agencies()
    assert out["total"] >= 90 and not out["truncated"]
    nypd = [a for a in out["results"] if a["slug"] == "nypd"]
    assert nypd and "NYPD" in nypd[0]["aliases"]
    filtered = list_agencies(query="consumer")
    assert filtered["total"] < out["total"]
    assert any(a["slug"] == "dcwp" for a in filtered["results"])
```

(If the YAML's police entry uses a different slug than `nypd`, check with `grep -B1 "NYPD" src/legistar_mcp/agencies.yaml` and substitute.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_vocab.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement**

Append to `src/legistar_mcp/tools/vocab.py`:

```python
def list_agencies(query: str | None = None) -> dict:
    """The agency dictionary behind search_bills/search_events `agency=`:
    95 hand-curated NYC agencies with the aliases the resolver accepts.
    Previously invisible to agents, who had to guess agency spellings."""
    from ._snippet import _get_agencies
    from ._validate import envelope

    entries = [
        {"slug": slug, "display": e["display"], "aliases": e.get("aliases", [e["display"]])}
        for slug, e in _get_agencies().items()
    ]
    if query:
        q = query.strip().lower()
        entries = [
            a for a in entries
            if q in a["slug"].lower()
            or q in a["display"].lower()
            or any(q in al.lower() for al in a["aliases"])
        ]
    entries.sort(key=lambda a: a["slug"])
    return envelope(entries, total=len(entries))
```

(Move the two imports to the module top.) Register in `server.py` next to `list_vocabulary`:

```python
    @server.tool(annotations=_RO)
    @_db_locked
    def list_agencies(
        query: Annotated[str | None, Field(description="Substring filter over slug, display name, and aliases (e.g. 'police', 'consumer').")] = None,
    ) -> dict:
        """The 95 NYC agencies the `agency=` parameter understands, with slug, display name, and accepted aliases. Call this before agency-filtered searches when unsure of a name; unmatched agency strings fall back to literal phrase search."""
        return _list_agencies(query=query)
```

with the import `from .tools.vocab import list_agencies as _list_agencies`. Also fix `vocab.py`'s `list_vocabulary` docstring line "call agencies.yaml directly" → "use the list_agencies tool" (agents cannot read server-side files).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tool_vocab.py tests/test_server.py -v`
Expected: all PASS (the Task-18 schema test automatically covers the new tool's descriptions).

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/ tests/test_tool_vocab.py
git commit -m "feat(tools): list_agencies — expose the agency alias dictionary to agents"
```

### Task 20: `data_status` tool + staleness warnings

**Files:**
- Create: `src/legistar_mcp/tools/status.py`
- Modify: `src/legistar_mcp/tools/bills.py` (`recent_bills` warning), `src/legistar_mcp/tools/events.py` (`upcoming_events` warning), `src/legistar_mcp/server.py` (register)
- Test: `tests/test_tool_status.py` (new)

**Interfaces:**
- Produces:
  - `data_status(conn) -> dict` with keys: `archive_root`, `last_indexed` (ISO or None), `index_age_days` (int or None), `schema_version_db`, `schema_version_code`, `counts` (`bills/events/people/votes/event_items`), `coverage` (`first_intro_date/last_intro_date/first_event_date/last_event_date`), `warnings` (list[str]).
  - `staleness_warning(conn, max_age_days: int = 7) -> str | None` (same module) — used by `recent_bills`/`upcoming_events`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_status.py`:

```python
import pytest
from freezegun import freeze_time

from legistar_mcp.db import init_db
from legistar_mcp.index.bulk import build_all
from legistar_mcp.tools.status import data_status, staleness_warning


@pytest.fixture
def indexed_db(tmp_path, fixtures_root):
    conn = init_db(tmp_path / "t.db")
    build_all(conn, archive_root=fixtures_root)
    return conn


def test_data_status_reports_counts_coverage_and_versions(indexed_db):
    s = data_status(indexed_db)
    assert s["counts"]["bills"] == 3
    assert s["counts"]["people"] == 1
    assert s["coverage"]["last_intro_date"] >= s["coverage"]["first_intro_date"]
    assert s["schema_version_db"] == s["schema_version_code"]
    assert s["last_indexed"] is not None
    assert s["index_age_days"] == 0
    assert s["warnings"] == []


def test_staleness_warning_after_a_week(indexed_db):
    # Freeze to a date far AFTER the fixture build so last_indexed is old.
    with freeze_time("2099-01-01"):
        w = staleness_warning(indexed_db)
        assert w is not None and "legistar-mcp index" in w
        s = data_status(indexed_db)
        assert any("legistar-mcp index" in x for x in s["warnings"])


def test_recent_and_upcoming_embed_warning_when_stale(indexed_db):
    from legistar_mcp.tools.bills import recent_bills
    from legistar_mcp.tools.events import upcoming_events
    with freeze_time("2099-01-01"):
        assert "warning" in recent_bills(indexed_db, days=30)
        assert "warning" in upcoming_events(indexed_db, days=30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tool_status.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement**

Create `src/legistar_mcp/tools/status.py`:

```python
"""Index freshness and coverage. The archive only updates when the user re-runs
`legistar-mcp index`; without this signal an agent answers "what's coming up /
what's new" confidently from a weeks-old snapshot."""
from __future__ import annotations

import datetime as _dt
from sqlite3 import Connection

from ..db import SCHEMA_VERSION
from ._snippet import _archive_root

_STALE_AFTER_DAYS = 7


def _last_indexed(conn: Connection) -> _dt.datetime | None:
    row = conn.execute(
        "SELECT value FROM index_state WHERE key = 'last_indexed'"
    ).fetchone()
    if row is None:
        return None
    try:
        return _dt.datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def _index_age_days(conn: Connection) -> int | None:
    stamp = _last_indexed(conn)
    if stamp is None:
        return None
    return max(0, (_dt.datetime.now(_dt.timezone.utc) - stamp).days)


def staleness_warning(conn: Connection, max_age_days: int = _STALE_AFTER_DAYS) -> str | None:
    """One-line warning when the index is old (or of unknown age). Embedded in
    time-window tools because those are where staleness silently lies."""
    age = _index_age_days(conn)
    if age is None:
        return (
            "Index age unknown (no last_indexed recorded — indexed by an older "
            "release). Refresh with `git pull` in the archive then `legistar-mcp index`."
        )
    if age > max_age_days:
        return (
            f"Index last updated {age} days ago — recent bills/hearings may be "
            f"missing. Refresh with `git pull` in the archive then `legistar-mcp index`."
        )
    return None


def data_status(conn: Connection) -> dict:
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("bills", "events", "people", "votes", "event_items")
    }
    cov = conn.execute(
        "SELECT MIN(intro_date), MAX(intro_date) FROM bills"
    ).fetchone()
    ecov = conn.execute("SELECT MIN(date), MAX(date) FROM events").fetchone()
    stamp = _last_indexed(conn)
    warnings = [w for w in (staleness_warning(conn),) if w]
    db_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if db_version < SCHEMA_VERSION:
        warnings.append(
            f"DB schema version {db_version} < code version {SCHEMA_VERSION}: "
            f"run `legistar-mcp index --full` to backfill."
        )
    root = _archive_root(conn)
    return {
        "archive_root": str(root) if root else None,
        "last_indexed": stamp.isoformat() if stamp else None,
        "index_age_days": _index_age_days(conn),
        "schema_version_db": db_version,
        "schema_version_code": SCHEMA_VERSION,
        "counts": counts,
        "coverage": {
            "first_intro_date": cov[0],
            "last_intro_date": cov[1],
            "first_event_date": ecov[0],
            "last_event_date": ecov[1],
        },
        "warnings": warnings,
    }
```

Wire the embedded warnings — in `bills.py` `recent_bills` and `events.py` `upcoming_events`, just before returning:

```python
    out = envelope(rows, total, offset)
    warning = staleness_warning(conn)
    if warning:
        out["warning"] = warning
    return out
```

(import `from .status import staleness_warning` — placed in `bills.py`/`events.py`; no circularity: `status.py` imports nothing from them). Register in `server.py`:

```python
    @server.tool(annotations=_RO)
    @_db_locked
    def data_status() -> dict:
        """Index freshness and coverage: when the local index was last built, how many bills/events/people/votes it holds, the date range covered, and any warnings (stale index, schema behind). Call this first when a question depends on recent or upcoming items."""
        return _data_status(conn)
```

with `from .tools.status import data_status as _data_status`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tool_status.py tests/test_server.py -v && uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src tests
git add src/legistar_mcp/ tests/test_tool_status.py
git commit -m "feat(tools): data_status + staleness warnings in recent/upcoming windows"
```

### Task 21: `get_bill_text` tool

**Files:**
- Modify: `src/legistar_mcp/tools/bills.py` (add `get_bill_text`), `src/legistar_mcp/server.py` (register)
- Test: `tests/test_tool_get_bill.py`

**Interfaces:**
- Produces: `get_bill_text(conn, archive_root, file=None, id=None, query=None, context_chars=1500, max_matches=5) -> dict` with keys `file`, `id`, `total_chars`, `truncated`, `segments` (list of `{"offset": int, "text": str}`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_get_bill.py`:

```python
def test_get_bill_text_targeted_passage(indexed_db, fixtures_root):
    from legistar_mcp.tools.bills import get_bill_text
    out = get_bill_text(
        indexed_db, fixtures_root, file="Int 0153-2022",
        query="office of operations", context_chars=200,
    )
    assert out["file"] == "Int 0153-2022"
    assert out["segments"], "phrase occurs in the bill text"
    assert all("office of operations" in s["text"].lower() for s in out["segments"])
    assert all(len(s["text"]) <= 200 * 2 + len("office of operations") for s in out["segments"])


def test_get_bill_text_head_mode_bounds_output(indexed_db, fixtures_root):
    from legistar_mcp.tools.bills import get_bill_text
    out = get_bill_text(indexed_db, fixtures_root, file="Int 0153-2022", context_chars=500)
    assert len(out["segments"]) == 1
    assert out["segments"][0]["offset"] == 0
    assert len(out["segments"][0]["text"]) <= 1000
    assert out["total_chars"] > 0


def test_get_bill_text_no_match_returns_empty_segments(indexed_db, fixtures_root):
    from legistar_mcp.tools.bills import get_bill_text
    out = get_bill_text(indexed_db, fixtures_root, file="Int 0153-2022", query="zzzunfindable")
    assert out["segments"] == [] and out["total_chars"] > 0
```

(Reuse the file's existing `indexed_db` fixture; if it doesn't define one, copy the standard fixture from `tests/test_tool_search_bills.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tool_get_bill.py -v`
Expected: FAIL — ImportError.

- [ ] **Step 3: Implement**

Append to `src/legistar_mcp/tools/bills.py`:

```python
def get_bill_text(
    conn: Connection,
    archive_root: Path,
    file: str | None = None,
    id: int | None = None,
    query: str | None = None,
    context_chars: int = 1500,
    max_matches: int = 5,
) -> dict:
    """Targeted extraction from a bill's statutory Text. The middle step
    between a 120-char search snippet and get_bill's full record (which can
    run to megabytes for omnibus bills): with `query`, windows around each
    case-insensitive occurrence; without, the head of the text."""
    import re as _re

    context_chars = max(100, min(context_chars, 5000))
    max_matches = max(1, min(max_matches, 20))
    bill_id = resolve_bill_id(conn, file, id)
    row = conn.execute(
        "SELECT file, path FROM bills WHERE id = ?", (bill_id,)
    ).fetchone()
    data = load_archive_json(archive_root, row["path"])
    text = data.get("Text") or ""

    segments: list[dict] = []
    if query and query.strip():
        for m in _re.finditer(_re.escape(query.strip()), text, _re.IGNORECASE):
            start = max(0, m.start() - context_chars)
            end = min(len(text), m.end() + context_chars)
            segments.append({"offset": start, "text": text[start:end]})
            if len(segments) >= max_matches:
                break
    elif text:
        segments.append({"offset": 0, "text": text[: context_chars * 2]})

    covered = sum(len(s["text"]) for s in segments)
    return {
        "file": row["file"],
        "id": bill_id,
        "total_chars": len(text),
        "truncated": covered < len(text),
        "segments": segments,
    }
```

(`resolve_bill_id` / `load_archive_json` imports already exist from Task 10 — extend that import line.) Register in `server.py`:

```python
    @server.tool(annotations=_RO)
    @_db_locked
    def get_bill_text(
        file: Annotated[str | None, Field(description="Bill file number, e.g. 'Int 0153-2022'.")] = None,
        id: Annotated[int | None, Field(description="Numeric bill ID.")] = None,
        query: Annotated[str | None, Field(description="Literal phrase to locate (case-insensitive). Omit to get the head of the text.")] = None,
        context_chars: Annotated[int, Field(description="Characters of context on each side of a match (100-5000).")] = 1500,
        max_matches: Annotated[int, Field(description="Max match windows to return (1-20).")] = 5,
    ) -> dict:
        """Extract passages from a bill's full statutory text without fetching the whole record: windows around each occurrence of `query`, or the head of the text if no query. Returns total_chars/truncated so you know how much text exists beyond the segments."""
        return _get_bill_text(
            conn, archive_root, file=file, id=id, query=query,
            context_chars=context_chars, max_matches=max_matches,
        )
```

with `from .tools.bills import get_bill_text as _get_bill_text`. Also add one line to `get_bill`'s wrapper description if not already present from Task 18 ("prefer get_bill_text for passages" — it is present).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tool_get_bill.py tests/test_server.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run pytest && uv run ruff check src tests
git add src/legistar_mcp/ tests/test_tool_get_bill.py
git commit -m "feat(tools): get_bill_text — bounded passage extraction from statutory text"
```

### Task 22: README, version bump, CI

**Files:**
- Modify: `README.md` (Tools table + envelope note), `pyproject.toml` (`version = "0.3.0"`)
- Create: `.github/workflows/ci.yml`

**Interfaces:** none (docs/meta).

- [ ] **Step 1: Update the README Tools table**

Add three rows to the `## Tools` table:

```markdown
| `list_agencies` | The 95 NYC agencies the `agency=` filter understands — slug, display name, aliases. Optional `query` substring filter. |
| `data_status` | Index freshness & coverage: last-indexed time, row counts, date range, warnings. Call first when recency matters. |
| `get_bill_text` | Bounded passages from a bill's full statutory text — windows around a `query` phrase, or the head of the text. |
```

Below the table, add:

```markdown
List tools return `{"results": [...], "total": N, "offset": N, "truncated": bool}` —
page with `offset`. Detail tools (`get_bill`, `get_event`, `get_person`) return
the record directly; `get_bill` can be very large, so prefer `get_bill_text`
for reading statutory text.
```

Also update the aggregate rows' filter descriptions (done partially in Task 7 — verify) and the two `Indexed:` output examples to the Task-14 format.

- [ ] **Step 2: Bump the version**

`pyproject.toml`: `version = "0.3.0"`.

- [ ] **Step 3: Add CI**

Create `.github/workflows/ci.yml`:

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv sync
      - run: uv run ruff check src tests
      - run: uv run pytest -v
```

(uv-managed CPython bundles SQLite ≥3.45, satisfying the 3.43 floor on CI.)

- [ ] **Step 4: Verify everything, commit**

```bash
uv run pytest && uv run ruff check src tests
git add README.md pyproject.toml .github/workflows/ci.yml
git commit -m "chore: v0.3.0 — README tool table, envelope docs, CI workflow"
```

**Milestone 4 checkpoint:** push `feat/agent-ergonomics`, open PR titled `feat: agent ergonomics — envelopes, descriptions, list_agencies/data_status/get_bill_text`.

---

## Final verification (after Milestone 4, before declaring done)

- [ ] `uv run pytest && uv run ruff check src tests` — green.
- [ ] Rebuild the real index and smoke-test the served tools end-to-end:
  ```bash
  cd ~/legistar/nyc_legislation && git pull
  cd ~/legistar && uv tool install --force --from "/Users/willhsiao/Desktop/Air Repo/Legistar-MCP" legistar-mcp
  legistar-mcp index --archive ./nyc_legislation --db ./legistar.db --full
  ```
  Expected: `Indexed: bills=… events=… people=… removed=…` with non-zero counts.
- [ ] Live probes through a fresh MCP client session (the traps from the review, now green):
  - `search_bills(query="covid-19")` → results, no error
  - `search_bills(status="enacted")` → same results as `"Enacted"`
  - `search_people(name="Adrienne Adams")` → finds `adrienne-e-adams`
  - `search_bills(query="housing", agency="NYPD", limit=3)` → narrower than agency alone
  - `search_bills(limit=-1)` → 1 result, envelope shape
  - `data_status()` → fresh timestamp, no warnings
- [ ] `/simplify` pass over the full diff (user's global rule for multi-step work).
- [ ] Run the plan's own Self-Review checklist against this document.

## Execution notes

- Run `/simplify` once per milestone PR (multi-step work rule), not per task.
- The user's live deployment (`~/legistar/legistar.db`, built 2026-05-25 at 301 MB under a pre-contentless schema) should be deleted and rebuilt `--full` after Milestone 3 lands — the migration path never converts an old fat FTS table to contentless, so a rebuild also reclaims ~200 MB.
